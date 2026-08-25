"""Capture one real hour of the org's Error Tracking issues as fixtures (F2, M8 1.1).

    make capture-errors
    make capture-errors ARGS="--hours 24 --slug errors_24h --query 'env:prod'"

The sibling of ``scripts/capture_datadog``, and for the same reason: everything
F2 is written against must be a payload the org actually returned, not the shape
the OpenAPI document implies. It writes ``tests/fixtures/datadog/errors/<slug>/``
— one ``search_<track>.json`` per track, one ``issue_<id>.json`` for the issues
the search named, one ``spans_<service>.json`` of the raw error spans behind the
loudest services, ``_calls.json`` with latency, size and every ``X-RateLimit-*``
header, and ``summary.md`` stating how many issues came back per track, how many
of them name a file and a function, and how many of the error spans carry the
exception the issue is about.

That last count is the point. F2 is only cheaper than F1 because an Error
Tracking issue already says where in the code the exception was raised; a capture
where most issues name no file is a capture that says F2 does not have its input.

Read-only: two search endpoints and one issue read, nothing that writes. Needs
``TRIAGE_DATADOG_SITE``, ``TRIAGE_DATADOG_API_KEY``, ``TRIAGE_DATADOG_APP_KEY``;
the application key must carry ``apm_read`` and ``error_tracking_read``.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.capture_datadog import Session, open_session
from triage.integrations.datadog import error_issue_search_body

FIXTURE_ROOT = Path("tests/fixtures/datadog/errors")
MAX_ISSUE_READS = 5
MAX_RECONSTRUCTIONS = 3
MAX_SPAN_CAPTURES = 4
SPAN_LIMIT = 20


def occurrences(
    session: Session, service: str, error_type: str | None, frm: datetime, to: datetime
) -> dict[str, Any]:
    """The raw error spans of one service, and how many carry this exception.

    The join is not a Datadog attribute. The platform runs the OpenTelemetry Java
    agent, so the exception type, message and stack live inside ``custom.events``
    — a JSON-encoded array of OTel span events — and ``@error.type`` is empty.
    This is the call F2's span collector makes, and the count it returns is the
    hit rate the collector's statuses are derived from (ADR-0029).
    """
    query = f"service:{service} status:error"
    body = session.call(
        f"spans.{service}",
        "POST",
        "/api/v2/spans/events/search",
        json={
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {"query": query, "from": frm.isoformat(), "to": to.isoformat()},
                    "page": {"limit": SPAN_LIMIT},
                    "sort": "-timestamp",
                },
            }
        },
    )
    spans = body.get("data", []) or []
    types: Counter[str] = Counter()
    stacks = 0
    for span in spans:
        for event in _exception_events(span):
            attributes = event.get("attributes", {}) or {}
            if attributes.get("exception.stacktrace"):
                stacks += 1
            if attributes.get("exception.type"):
                types[str(attributes["exception.type"])] += 1
    return {
        "service": service,
        "query": query,
        "error_type": error_type,
        "spans": len(spans),
        "with_stack": stacks,
        "matching": types.get(error_type or "", 0),
        "types": dict(types),
        "body": body,
    }


def _exception_events(span: dict[str, Any]) -> list[dict[str, Any]]:
    raw = ((span.get("attributes", {}) or {}).get("custom", {}) or {}).get("events")
    if not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("name") == "exception"]


def reconstruct(
    session: Session, issue: dict[str, Any], frm: datetime, to: datetime
) -> dict[str, Any]:
    """What a query built from the issue's own fields finds, against what it claims.

    Datadog documents no attribute joining a span or a log back to the issue it
    was grouped into, so Phase 3 has to rebuild the query. Whether that
    reconstruction can find the occurrences at all is not a design question, it
    is a measurement, and this is where it is taken.
    """
    query = f'service:{issue["service"]} @error.type:"{issue["error_type"]}"'
    spans = session.call(
        "spans.reconstruct",
        "POST",
        "/api/v2/spans/analytics/aggregate",
        json={
            "data": {
                "type": "aggregate_request",
                "attributes": {
                    "filter": {"query": query, "from": frm.isoformat(), "to": to.isoformat()},
                    "compute": [{"aggregation": "count", "type": "total"}],
                },
            }
        },
    )
    logs = session.call(
        "logs.reconstruct",
        "POST",
        "/api/v2/logs/analytics/aggregate",
        json={
            "filter": {
                "query": query,
                "from": int(frm.timestamp() * 1000),
                "to": int(to.timestamp() * 1000),
            },
            "compute": [{"aggregation": "count", "type": "total"}],
        },
    )
    return {
        "issue_id": issue["issue_id"],
        "query": query,
        "claimed": issue.get("total_count"),
        "spans": _total(bucket.get("compute") for bucket in spans.get("data", []) or []),
        "logs": _total(
            bucket.get("computes") for bucket in (logs.get("data") or {}).get("buckets", []) or []
        ),
    }


def _total(computes: Any) -> int | None:
    for compute in computes:
        if isinstance(compute, dict) and "c0" in compute:
            value = compute["c0"]
            return int(value) if isinstance(value, int | float) else None
    return None


def search(session: Session, track: str, persona: str, query: str, frm: datetime, to: datetime):
    return session.call(
        f"error_issues.{track}",
        "POST",
        "/api/v2/error-tracking/issues/search",
        params={"include": "issue"},
        json=error_issue_search_body(query=query, frm=frm, to=to, track=track, persona=persona),
    )


def issues_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``included`` issue objects, joined to the counts the search ranked them by."""
    counts = {
        entry.get("id"): entry.get("attributes", {}).get("total_count")
        for entry in body.get("data", []) or []
    }
    joined = []
    for issue in body.get("included", []) or []:
        attributes = dict(issue.get("attributes", {}) or {})
        attributes["issue_id"] = issue.get("id")
        attributes["total_count"] = counts.get(issue.get("id"))
        joined.append(attributes)
    return joined


def summarise(
    slug: str,
    frm: datetime,
    to: datetime,
    query: str,
    persona: str,
    found: dict[str, list],
    rebuilt: list[dict[str, Any]],
    sampled: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Error Tracking capture — {slug}",
        "",
        f"Captured {datetime.now(UTC).isoformat()} by `make capture-errors`, read-only.",
        f"Window `{frm.isoformat()}` .. `{to.isoformat()}` "
        f"({(to - frm).total_seconds() / 3600:.0f} h), "
        f"query `{query}`, persona `{persona}`.",
        "",
        "| track | issues | name a file | name a function | name both | with a version |",
        "|---|---|---|---|---|---|",
    ]
    for track, issues in found.items():
        files = sum(1 for i in issues if i.get("file_path"))
        functions = sum(1 for i in issues if i.get("function_name"))
        both = sum(1 for i in issues if i.get("file_path") and i.get("function_name"))
        versions = sum(1 for i in issues if i.get("first_seen_version"))
        lines.append(f"| {track} | {len(issues)} | {files} | {functions} | {both} | {versions} |")
    for track, issues in found.items():
        if not issues:
            continue
        lines += ["", f"## {track}", ""]
        states = Counter(i.get("state") for i in issues)
        services = Counter(i.get("service") for i in issues)
        types = Counter(i.get("error_type") for i in issues)
        regressed = [i for i in issues if i.get("regression")]
        lines += [
            f"- states: {dict(states)}",
            f"- {len(services)} distinct services, {len(types)} distinct exception types",
            f"- {len(regressed)} carry a `regression` block",
            f"- top exception types: {types.most_common(5)}",
        ]
    if rebuilt:
        lines += [
            "",
            "## The reconstructed query, measured",
            "",
            "No attribute joins an occurrence back to its issue, so a collector has to",
            "rebuild the query from the issue's own fields. What that finds, against what",
            "the issue claims over the same window:",
            "",
            "| issue | query | issue claims | spans found | logs found |",
            "|---|---|---|---|---|",
        ]
        for row in rebuilt:
            lines.append(
                f"| `{row['issue_id'][:8]}` | `{row['query']}` | {row['claimed']} | "
                f"{row['spans']} | {row['logs']} |"
            )
    if sampled:
        lines += [
            "",
            "## The occurrences, found by the query that works",
            "",
            "`service:X status:error` over raw spans, joined on `exception.type` inside the",
            "JSON string `custom.events` — the OpenTelemetry span events, where this platform",
            "puts the type, the message and the stack. `@error.type` is empty here.",
            "",
            "| service | error spans | with an OTel stack | the issue's type | matching | "
            "types actually retained |",
            "|---|---|---|---|---|---|",
        ]
        for row in sampled:
            found_types = ", ".join(f"`{name}` x{count}" for name, count in row["types"].items())
            lines.append(
                f"| `{row['service']}` | {row['spans']} | {row['with_stack']} | "
                f"`{row['error_type']}` | {row['matching']} | {found_types or '—'} |"
            )
    limited = [call for call in calls if call.get("ratelimit")]
    lines += [
        "",
        "## Rate limits",
        "",
        f"{len(limited)} of {len(calls)} calls carried an `X-RateLimit-*` header. "
        "Error Tracking published none.",
    ]
    return "\n".join(lines) + "\n"


def cmd_capture(hours: float, slug: str, tracks: list[str], persona: str, query: str) -> int:
    to = datetime.now(UTC)
    frm = to - timedelta(hours=hours)
    out = FIXTURE_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)

    def save(name: str, payload: Any) -> None:
        (out / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    print(f"window {frm.isoformat()} .. {to.isoformat()}  ->  {out}")
    session = open_session()
    found: dict[str, list[dict[str, Any]]] = {}
    rebuilt: list[dict[str, Any]] = []
    sampled: list[dict[str, Any]] = []
    with session.client:
        for track in tracks:
            body = search(session, track, persona, query, frm, to)
            save(f"search_{track}", body)
            found[track] = issues_of(body)
            print(f"  {track:26s} {len(found[track])} issues")

        # One issue read per track, to record what the detail endpoint adds over
        # what the search already returned — if it adds nothing, F2 never calls it.
        for track, issues in found.items():
            for issue in issues[:MAX_ISSUE_READS]:
                issue_id = issue["issue_id"]
                save(
                    f"issue_{issue_id}",
                    session.call(
                        f"error_issue.{track}",
                        "GET",
                        f"/api/v2/error-tracking/issues/{issue_id}",
                    ),
                )

        ranked = next((issues for issues in found.values() if issues), [])
        for issue in ranked[:MAX_RECONSTRUCTIONS]:
            rebuilt.append(reconstruct(session, issue, frm, to))
        if rebuilt:
            save("_reconstruction", rebuilt)

        seen: set[str] = set()
        for issue in ranked:
            service = issue.get("service")
            if not service or service in seen or len(seen) >= MAX_SPAN_CAPTURES:
                continue
            seen.add(service)
            row = occurrences(session, service, issue.get("error_type"), frm, to)
            save(f"spans_{service}", row.pop("body"))
            sampled.append(row)
            print(
                f"  {service:26s} {row['spans']} error spans, {row['with_stack']} with a stack, "
                f"{row['matching']} matching {row['error_type']}"
            )
        if sampled:
            save("_occurrences", sampled)

    save("_calls", session.calls)
    (out / "summary.md").write_text(
        summarise(slug, frm, to, query, persona, found, rebuilt, sampled, session.calls)
    )
    print(f"\n{len(session.calls)} calls -> {out}")
    print((out / "summary.md").read_text())
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="capture_errors", description=__doc__)
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--slug", default=None, help="fixture directory name")
    parser.add_argument(
        "--track",
        action="append",
        default=None,
        dest="tracks",
        help="repeatable; defaults to trace and logs",
    )
    parser.add_argument("--persona", default="BACKEND")
    parser.add_argument(
        "--query",
        default="*",
        help="Datadog query. Empty is refused by the API, so '*' is the no-filter pass.",
    )
    args = parser.parse_args(argv[1:])
    slug = args.slug or f"org_{datetime.now(UTC):%Y%m%d}_{args.hours:g}h"
    return cmd_capture(args.hours, slug, args.tracks or ["trace", "logs"], args.persona, args.query)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
