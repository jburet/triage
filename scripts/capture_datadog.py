"""Capture real Datadog responses as fixtures, and explore an incident by hand.

    uv run python -m scripts.capture_datadog find "Statefulset Replicas"
    uv run python -m scripts.capture_datadog triggers 18369851 --days 30
    uv run python -m scripts.capture_datadog capture 18369851 2026-08-22T00:44:00Z \
        --slug hcl_software_uat_20260822 \
        --scope service:plt-hcl-software-uat --scope kube_namespace:hcl-software-uat

Two jobs, one tool. It writes ``tests/fixtures/datadog/<slug>/``, which is what
``FakeDatadogClient`` replays and therefore the only reason the test suite can stay
offline while describing real payloads. And it is how a new alert class gets its recipe:
M3 Phase 2 is written from one captured incident, and the open risk on that plan is that
the other classes have none yet (see ``docs/plans/2026-08-23-m3-analysis-and-incidents.md``).

Read-only against Datadog: monitor reads and search endpoints, nothing that writes.
Needs ``TRIAGE_DATADOG_SITE``, ``TRIAGE_DATADOG_API_KEY``, ``TRIAGE_DATADOG_APP_KEY``
(see ``datadog.env.example``); the application key must carry ``monitors_read``,
``timeseries_query``, ``logs_read_data``, ``apm_read`` and ``events_read``.

Datadog publishes no per-endpoint rate limits, so every call records its ``X-RateLimit-*``
headers into ``_calls.json`` alongside latency and size — the first time we are throttled,
the evidence is already there.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import httpx

from triage.config import get_settings

FIXTURE_ROOT = Path("tests/fixtures/datadog")
LOG_RETENTION_DAYS = 15

# A monitor's own query is the best description of what broke, but it is only re-runnable
# in the idiom it was written in: a metric monitor's threshold expression goes to the
# timeseries API, an event monitor's goes to the event search API. Guessing wrong gets a
# 400 and an empty fixture that looks like "no data".
METRIC_QUERY = re.compile(r"^\w+\([^)]*\):(?P<expr>.+?)\s*(?:<=|>=|<|>|==|!=)\s*[-\d.]+\s*$", re.S)
EVENT_QUERY = re.compile(r'^events\("(?P<expr>.*?)"\)\.', re.S)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@dataclass
class Session:
    """One Datadog client plus the log of what was asked of it."""

    client: httpx.Client
    calls: list[dict[str, Any]] = field(default_factory=list)

    def call(self, name: str, method: str, path: str, **kwargs: Any) -> Any:
        started = monotonic()
        response = self.client.request(method, path, **kwargs)
        if response.status_code == 429:
            # Measured 2026-08-23: spans 5/60 s, logs search 3/10 s, logs aggregate 2/10 s.
            # Tight enough that a sweep throttles itself, so honour the reset and retry once.
            pause = float(response.headers.get("x-ratelimit-reset", "5")) + 1
            print(f"  {name:26s} 429, waiting {pause:.0f}s")
            sleep(pause)
            response = self.client.request(method, path, **kwargs)
        elapsed = round(monotonic() - started, 3)
        content_type = response.headers.get("content-type", "")
        body = response.json() if content_type.startswith("application/json") else {}
        self.calls.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
                "status": response.status_code,
                "seconds": elapsed,
                "bytes": len(response.content),
                "ratelimit": {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower().startswith("x-ratelimit")
                },
            }
        )
        print(f"  {name:26s} {response.status_code} {elapsed:6.2f}s {len(response.content):>9,d}B")
        if response.status_code >= 400:
            print(f"    {response.text[:300]}", file=sys.stderr)
        return body


def open_session() -> Session:
    settings = get_settings()
    if not (settings.datadog_api_key and settings.datadog_app_key):
        raise SystemExit(
            "TRIAGE_DATADOG_API_KEY / TRIAGE_DATADOG_APP_KEY are unset — see datadog.env.example"
        )
    return Session(
        httpx.Client(
            base_url=f"https://{settings.datadog_site}",
            headers={
                "DD-API-KEY": settings.datadog_api_key,
                "DD-APPLICATION-KEY": settings.datadog_app_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
    )


def search_events(session: Session, name: str, query: str, frm: datetime, to: datetime) -> Any:
    return session.call(
        name,
        "POST",
        "/api/v2/events/search",
        json={
            "filter": {"query": query, "from": millis(frm), "to": millis(to)},
            "page": {"limit": 200},
            "sort": "timestamp",
        },
    )


def cmd_find(term: str) -> int:
    """Slack gives a monitor name; everything else needs its id."""
    session = open_session()
    with session.client:
        body = session.call(
            "monitor.search", "GET", "/api/v1/monitor/search", params={"query": term}
        )
    for monitor in body.get("monitors", []):
        print(f"{monitor.get('id'):>10}  {monitor.get('status', ''):10} {monitor.get('name')}")
    return 0


def cmd_triggers(monitor_id: int, days: int) -> int:
    """When did it actually fire, and for which group? A pasted alert has no timestamp."""
    now = datetime.now(UTC)
    session = open_session()
    with session.client:
        body = search_events(
            session, "events.triggers", f"@monitor.id:{monitor_id}", now - timedelta(days=days), now
        )
    for event in body.get("data", []):
        attributes = event.get("attributes", {})
        inner = attributes.get("attributes", {})
        groups = ",".join(inner.get("monitor", {}).get("groups") or [])
        print(f"{attributes.get('timestamp')}  {inner.get('status')!s:8} {groups}")
    return 0


def cmd_capture(
    monitor_id: int,
    trigger: str,
    slug: str,
    scopes: list[str],
    metrics: list[str],
    before: int,
    after: int,
) -> int:
    """Everything the sweep would collect, saved verbatim.

    Both event scopes because the probe failures and container exit codes that explained
    the reference incident were absent at ``service:`` scope and present at namespace scope.
    """
    at = parse_time(trigger)
    frm, to = at - timedelta(minutes=before), at + timedelta(minutes=after)
    age = (datetime.now(UTC) - at).days
    if age > LOG_RETENTION_DAYS:
        print(
            f"warning: the alert is {age} days old; logs and spans are typically retained "
            f"for {LOG_RETENTION_DAYS} days, so those collectors will look empty for the "
            "wrong reason",
            file=sys.stderr,
        )

    out = FIXTURE_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)

    def save(name: str, payload: Any) -> None:
        (out / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    print(f"window {frm.isoformat()} .. {to.isoformat()}  ->  {out}")
    session = open_session()
    with session.client:
        monitor = session.call(
            "monitor.get", "GET", f"/api/v1/monitor/{monitor_id}", params={"group_states": "all"}
        )
        save("monitor", monitor)

        query = str(monitor.get("query", ""))
        metric_match = METRIC_QUERY.match(query)
        event_match = EVENT_QUERY.match(query)
        if metric_match:
            save(
                "monitor_query_timeseries",
                session.call(
                    "metrics.monitor_query",
                    "GET",
                    "/api/v1/query",
                    params={
                        "from": int(frm.timestamp()),
                        "to": int(to.timestamp()),
                        "query": metric_match.group("expr").strip(),
                    },
                ),
            )
        elif event_match:
            save(
                "monitor_query_events",
                search_events(
                    session,
                    "events.monitor_query",
                    # The monitor embeds its event query as a quoted string, so the inner
                    # quotes arrive escaped and Datadog rejects them on the way back in.
                    event_match.group("expr").replace('\\"', '"'),
                    frm,
                    to,
                ),
            )
        else:
            print(f"  monitor query not re-runnable ({monitor.get('type')}): {query[:70]}")

        for index, scope in enumerate(scopes):
            suffix = scope.split(":", 1)[0]
            save(f"events_{suffix}", search_events(session, f"events.{suffix}", scope, frm, to))
            if index > 0:
                continue
            save(
                "logs_aggregate",
                session.call(
                    "logs.aggregate",
                    "POST",
                    "/api/v2/logs/analytics/aggregate",
                    json={
                        "filter": {"query": scope, "from": millis(frm), "to": millis(to)},
                        "compute": [{"aggregation": "count", "type": "total"}],
                        "group_by": [{"facet": "status", "limit": 10}],
                    },
                ),
            )
            save(
                "logs_at_alert",
                session.call(
                    "logs.at_alert",
                    "POST",
                    "/api/v2/logs/events/search",
                    json={
                        "filter": {"query": scope, "from": millis(frm), "to": millis(to)},
                        "page": {"limit": 60},
                        "sort": "-timestamp",
                    },
                ),
            )

        # A class recipe asks for more than the monitor's own query: restarts and replica
        # counts are what turn "the pod was down" into "it was killed three times".
        for metric in metrics:
            save(
                f"metric_{re.sub(r'[^a-z0-9]+', '_', metric.split('{')[0].split(':')[-1])}",
                session.call(
                    "metrics.extra",
                    "GET",
                    "/api/v1/query",
                    params={
                        "from": int(frm.timestamp()),
                        "to": int(to.timestamp()),
                        "query": metric,
                    },
                ),
            )

        # Span presence, twice: in the window, then wide. Empty in both means the workload
        # is not instrumented; empty only in the window is evidence about the incident.
        for name, scope, span_from in (
            ("spans_window", scopes[0], frm),
            ("spans_wide", scopes[-1], at - timedelta(days=7)),
        ):
            save(
                name,
                session.call(
                    f"spans.{name}",
                    "POST",
                    "/api/v2/spans/analytics/aggregate",
                    json={
                        "data": {
                            "type": "aggregate_request",
                            "attributes": {
                                "filter": {
                                    "query": scope,
                                    "from": span_from.isoformat(),
                                    "to": to.isoformat(),
                                },
                                "compute": [{"aggregation": "count", "type": "total"}],
                                "group_by": [{"facet": "service", "limit": 10}],
                            },
                        }
                    },
                ),
            )

    save("_calls", session.calls)
    total = sum(int(call["bytes"]) for call in session.calls)
    print(f"\n{len(session.calls)} calls, {total:,d} bytes raw -> {out}")
    print("reduction is the design: see collection caps in the M3 plan")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="capture_datadog", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    finder = sub.add_parser("find", help="locate a monitor by name")
    finder.add_argument("term")

    triggers = sub.add_parser("triggers", help="list a monitor's firings and their groups")
    triggers.add_argument("monitor_id", type=int)
    triggers.add_argument("--days", type=int, default=30)

    capture = sub.add_parser("capture", help="save one firing's telemetry as fixtures")
    capture.add_argument("monitor_id", type=int)
    capture.add_argument("trigger", help="ISO-8601, e.g. 2026-08-22T00:44:00Z")
    capture.add_argument("--slug", required=True, help="fixture directory name")
    capture.add_argument(
        "--scope",
        action="append",
        required=True,
        dest="scopes",
        help="repeatable; the first is used for logs, all are used for events",
    )
    capture.add_argument(
        "--metric",
        action="append",
        default=[],
        dest="metrics",
        help="repeatable; an extra Datadog metric query to capture over the window",
    )
    capture.add_argument("--before", type=int, default=45, help="minutes before the trigger")
    capture.add_argument("--after", type=int, default=30, help="minutes after the trigger")

    args = parser.parse_args(argv[1:])
    if args.command == "find":
        return cmd_find(args.term)
    if args.command == "triggers":
        return cmd_triggers(args.monitor_id, args.days)
    return cmd_capture(
        args.monitor_id,
        args.trigger,
        args.slug,
        args.scopes,
        args.metrics,
        args.before,
        args.after,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
