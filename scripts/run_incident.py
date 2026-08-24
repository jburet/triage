"""One real alert, end to end, with every intermediate result printed.

    make run-incident ARGS="--find 'Statefulset Replicas'"
    make run-incident ARGS="--monitor 18369851"
    make run-incident ARGS="--monitor 18369851 --at 2026-08-23T09:12:00Z"
    make run-incident ARGS="--monitor 18369851 --group kube_namespace:hcl-software-uat"
    make run-incident ARGS="--monitor 18369851 \
        --map plt-hcl-software-uat=github.com/org/platform@abc1234"

This is the one-shot: it finds a real firing of a real monitor, then runs the
actual ``incident`` graph over it and prints what each node produced — the alert
class and the window, every collector and what it returned, the follow-up calls,
the ranked causes, the analyses, the diagnosis, the ticket draft and the
post-mortem. It streams node updates rather than re-implementing the flow, so
what is printed is what the graph did and not a second description of it.

**What it touches.** Datadog: read-only, and every call is logged with its
latency and size. LiteLLM: the real tiers, so it costs money — roughly one
``triage`` call, one or two ``analysis`` calls and one ``diagnosis`` call, plus
compose and review. Jira and Slack: recording fakes, refused outright unless
``TRIAGE_DRY_RUN`` is on. Postgres: not touched at all unless ``--db``.

**What will look wrong, and is not.** With no system map the qualification's
causes cannot resolve a commit, so they arrive as dependency causes and no
analysis Job is submitted — seed one with ``--map`` or read the real map with
``--db``. And the investigative analyses have no entrypoint yet (architecture
§10), so a hypothesis that *is* routed to one comes back as a stated failure;
the diagnosis records it as an unknown and caps its confidence, which is the
designed behaviour rather than a bug in this run.
"""

import argparse
import asyncio
import json
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from triage.collect.recipes import collection_window
from triage.config import get_config, get_settings
from triage.db.repo import InMemoryRepository, SystemMapEntry
from triage.graphs.incident import build_graph
from triage.integrations.datadog import DatadogClient, DatadogRestClient
from triage.nodes.collect import classify_alert, collect
from triage.runtime import DEPS_KEY, Deps, build_deps, build_github
from triage.schemas.alert import Alert, AlertStatus
from triage.schemas.collection import AlertClass, AlertClassification, Collection
from triage.schemas.common import render as render_field
from triage.schemas.system_map import RepoSummary, ServiceEntry, SystemMapKind
from triage.scope import resolve

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"

ANALYSIS_ENTRYPOINT = [sys.executable, "-m", "triage.analysis.entrypoint"]

SUBGRAPHS = frozenset({"analysis", "ticket_pipeline"})
"""Their inner nodes stream separately; the parent update is the same thing again."""


def rule(title: str) -> None:
    print(f"\n{BOLD}── {title} {'─' * max(0, 70 - len(title))}{RESET}")


def indent(text: str, prefix: str = "  ") -> str:
    return textwrap.indent(str(text).rstrip(), prefix)


def clip(text: object, limit: int = 160) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


@dataclass
class RecordedCall:
    endpoint: str
    query: str
    seconds: float
    items: int


@dataclass
class Recorder:
    """Wraps the real client so every call it makes is visible afterwards.

    The point of ADR-0016 is that every fact in a ticket comes from a call we
    made and can show. This is the "and can show" half.
    """

    inner: DatadogClient
    calls: list[RecordedCall] = field(default_factory=list)

    async def _run(self, endpoint: str, query: str, coro: Any) -> dict[str, Any]:
        started = time.monotonic()
        try:
            payload: dict[str, Any] = await coro
        except Exception as exc:
            self.calls.append(
                RecordedCall(endpoint, f"{query}  !! {exc}", time.monotonic() - started, 0)
            )
            raise
        data = payload.get("data")
        items = len(data) if isinstance(data, list) else len(payload.get("series", []) or [])
        self.calls.append(RecordedCall(endpoint, query, time.monotonic() - started, items))
        return payload

    async def search_events(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run("events", kwargs["query"], self.inner.search_events(**kwargs))

    async def get_monitor(self, monitor_id: int) -> dict[str, Any]:
        return await self._run("monitor", str(monitor_id), self.inner.get_monitor(monitor_id))

    async def query_timeseries(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run("metrics", kwargs["query"], self.inner.query_timeseries(**kwargs))

    async def aggregate_logs(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run(
            "logs.aggregate", kwargs["query"], self.inner.aggregate_logs(**kwargs)
        )

    async def search_logs(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run("logs.search", kwargs["query"], self.inner.search_logs(**kwargs))

    async def aggregate_spans(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run("spans", kwargs["query"], self.inner.aggregate_spans(**kwargs))


@dataclass
class ModelCall:
    tier: str
    schema: str
    chars: int
    seconds: float
    failed: str | None = None


@dataclass
class PromptSpy:
    """Prints the exact text sent to each tier, before the answer comes back.

    The prompts are assembled from files plus tagged JSON blocks, so what a node
    actually asked is not readable from the source alone — and when a run says
    something surprising, the prompt is the first thing worth seeing.
    """

    inner: Any
    show: bool = True
    limit: int | None = None
    calls: list[ModelCall] = field(default_factory=list)

    async def call(self, tier: str, prompt: str, schema: type) -> Any:
        if self.show:
            rule(f"prompt → {tier} tier · {schema.__name__} ({len(prompt)} chars)")
            body = prompt if self.limit is None else prompt[: self.limit]
            print(indent(body))
            if self.limit is not None and len(prompt) > self.limit:
                print(indent(f"{DIM}… {len(prompt) - self.limit} more chars{RESET}"))
        started = time.monotonic()
        try:
            answer = await self.inner.call(tier, prompt, schema)
        except Exception as exc:
            self.calls.append(
                ModelCall(tier, schema.__name__, len(prompt), time.monotonic() - started, str(exc))
            )
            raise
        self.calls.append(ModelCall(tier, schema.__name__, len(prompt), time.monotonic() - started))
        return answer


async def find_monitor(settings_site: str, api_key: str, app_key: str, term: str) -> int | None:
    """Monitor id from a name, because what arrives from Slack is a name.

    One match is used; several are listed and the run stops — picking one for you
    would be picking which incident you meant.
    """
    import httpx

    async with httpx.AsyncClient(
        base_url=f"https://{settings_site}",
        headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key},
        timeout=30.0,
    ) as client:
        response = await client.get("/api/v1/monitor/search", params={"query": term})
        response.raise_for_status()
        monitors = response.json().get("monitors", [])

    if len(monitors) == 1:
        print(indent(f"{monitors[0]['id']}  {monitors[0].get('name')}"))
        return int(monitors[0]["id"])
    rule(f"{len(monitors)} monitors match {term!r}")
    for monitor in monitors[:25]:
        print(
            indent(f"{monitor.get('id'):>10}  {monitor.get('status', ''):10} {monitor.get('name')}")
        )
    return None


async def find_alert(
    client: DatadogClient, monitor_id: int, at: datetime | None, hours: int, group: str | None
) -> tuple[Alert | None, list[Alert]]:
    """The firing to replay, and every transition around it.

    Nearest to ``--at`` when given, otherwise the most recent firing: a monitor
    that fires for 66 tenant groups has many, and picking one by hand from a
    Slack paste is exactly the work this is meant to remove.
    """
    now = datetime.now(UTC)
    frm = (at or now) - timedelta(hours=hours)
    to = min(now, (at + timedelta(hours=hours)) if at else now)
    page = await client.search_events(query=f"@monitor.id:{monitor_id}", frm=frm, to=to, limit=200)
    transitions = [
        Alert.from_event(event)
        for event in page.get("data", []) or []
        if Alert.is_monitor_alert(event)
    ]
    firing = [alert for alert in transitions if alert.status.is_firing]
    if group:
        firing = [alert for alert in firing if group in (alert.group or "")]
    if not firing:
        return None, transitions
    if at is not None:
        chosen = min(firing, key=lambda alert: abs(alert.fired_at - at))
    else:
        chosen = max(firing, key=lambda alert: alert.fired_at)
    return chosen, transitions


def recovery_of(alert: Alert, transitions: list[Alert]) -> Alert | None:
    return next(
        (
            other
            for other in sorted(transitions, key=lambda item: item.fired_at)
            if other.status is AlertStatus.OK
            and other.group == alert.group
            and other.fired_at > alert.fired_at
        ),
        None,
    )


def seed_map(repo: InMemoryRepository, mappings: list[str]) -> None:
    """``--map service=repo_url[@commit]``: the minimum F0 would have discovered.

    Enough for ``qualify`` to resolve a commit and for the fan-out to submit a
    real request. It is not a substitute for cartography — the summary is empty —
    but without it every cause degrades to a dependency and nothing is analysed.
    """
    for mapping in mappings:
        service, _, target = mapping.partition("=")
        repo_url, _, commit = target.partition("@")
        entry = ServiceEntry(
            name=service,
            repo_url=repo_url,
            source_commit=commit or None,
            summary=RepoSummary(
                repo_url=repo_url,
                service=service,
                languages={"unknown": True, "reason": "seeded by --map, not summarised by F0"},
                frameworks={"unknown": True, "reason": "seeded by --map, not summarised by F0"},
                entry_points={"unknown": True, "reason": "seeded by --map, not summarised by F0"},
                endpoints={"unknown": True, "reason": "seeded by --map, not summarised by F0"},
                depends_on={"unknown": True, "reason": "seeded by --map, not summarised by F0"},
                database_access={
                    "unknown": True,
                    "reason": "seeded by --map, not summarised by F0",
                },
                observability={"unknown": True, "reason": "seeded by --map, not summarised by F0"},
            ),
        )
        repo.system_map[(SystemMapKind.SERVICE, service)] = SystemMapEntry(
            kind=SystemMapKind.SERVICE,
            name=service,
            team=None,
            source_commit=commit or None,
            payload=entry.model_dump(mode="json"),
        )


def show_alert(alert: Alert, transitions: list[Alert]) -> None:
    rule("the alert")
    print(indent(f"monitor  {alert.monitor_id} — {alert.monitor_name}"))
    print(indent(f"group    {alert.group or '(none)'}"))
    print(indent(f"fired    {alert.fired_at.isoformat()}  (event {alert.event_id})"))
    print(indent(f"priority {alert.priority}   status {alert.status.value}"))
    print(indent(f"scope    {alert.scope.model_dump(exclude_none=True)}"))
    print(indent(f"query    {clip(alert.monitor_query, 200)}"))
    print(indent(f"{DIM}{len(transitions)} transitions in the searched window{RESET}"))


def show_gate(alert: Alert, deps: Deps, recovery: Alert | None) -> None:
    rule("scope and the persistence gate")
    routing = resolve(deps.config, alert)
    print(indent(f"team        {routing.team or '—'}   environment {routing.environment or '—'}"))
    print(indent(f"in scope    {routing.in_scope}  ({routing.reason})"))
    minutes = deps.config.persistence_minutes(routing.team, alert.priority)
    if recovery is not None:
        lasted = (recovery.fired_at - alert.fired_at).total_seconds() / 60
        verdict = (
            f"self_recovered after {lasted:.1f} min — the gate ({minutes} min) would have "
            f"discarded it, and the flap counter would have counted it"
            if lasted < minutes
            else f"recovered after {lasted:.1f} min, past the {minutes} min gate: analysed"
        )
    else:
        age = (datetime.now(UTC) - alert.fired_at).total_seconds() / 60
        verdict = f"no recovery in the window; {age:.0f} min old against a {minutes} min gate"
    print(indent(f"gate        {verdict}"))
    print(indent(f"{DIM}replaying it regardless — this run is the analysis, not the poller{RESET}"))


def show_collection(collection: Collection) -> None:
    print(indent(f"class {collection.alert_class.value}   window {collection.window}"))
    for result in collection.results:
        head = f"{result.status.value:17s} {result.collector.value:20s} {clip(result.query, 70)}"
        print(indent(head, "    "))
        if result.detail:
            print(indent(f"{DIM}{clip(result.detail, 140)}{RESET}", "      "))
        payload = result.payload
        if payload.get("events"):
            for event in payload["events"][:4]:
                line = event.get("message") or event.get("title")
                print(indent(f"· {event.get('at')} {clip(line, 110)}", "      "))
            if "change" in str(payload):
                for event in payload["events"]:
                    change = event.get("change")
                    if change:
                        print(
                            indent(
                                f"Δ {list(change['changed_fields'])} — {change['verdict']}",
                                "      ",
                            )
                        )
        for template in (payload.get("templates") or [])[:5]:
            print(
                indent(
                    f"x {template['count']:4d} [{template['status']}] "
                    f"{clip(template['template'], 95)}",
                    "      ",
                )
            )
        for series in (payload.get("series") or [])[:4]:
            print(
                indent(
                    f"~ {clip(series.get('metric'), 60)}  min {series.get('min')} "
                    f"max {series.get('max')} first {series.get('first')} "
                    f"last {series.get('last')}",
                    "      ",
                )
            )
    if collection.refused:
        print(indent(f"refused: {collection.refused}", "    "))


def show_update(node: str, update: dict[str, Any]) -> None:
    """One node's contribution, rendered by what it actually produced."""
    rule(f"node: {node}")
    if "classification" in update:
        classification = update["classification"]
        print(indent(f"class  {classification.alert_class.value}"))
        print(indent(f"why    {classification.reason}"))
    if "window" in update:
        print(indent(f"window {update['window']}"))
    if "collection" in update:
        show_collection(update["collection"])
    if "followup_done" in update and "collection" not in update:
        print(indent(f"follow-up finished: {update['followup_done']}"))
    if "qualification" in update:
        print(indent(f"summary: {update['qualification'].summary}"))
        for cause in sorted(
            update["qualification"].causes, key=lambda item: item.rank_score, reverse=True
        ):
            print(
                indent(
                    f"{cause.rank_score:.2f} [{cause.cause_type.value}] {cause.service}: "
                    f"{clip(cause.description, 120)}",
                    "    ",
                )
            )
    if "hypotheses" in update:
        for hypothesis in update["hypotheses"]:
            print(
                indent(
                    f"→ [{hypothesis.cause_type.value}] {hypothesis.service} "
                    f"@ {hypothesis.commit or 'no commit'}",
                    "    ",
                )
            )
    if "selected" in update:
        print(indent(f"analysing {len(update['selected'])} hypotheses"))
        for item in update.get("deferred", []):
            print(
                indent(
                    f"not analysed: {clip(item.hypothesis.description, 80)} — {item.reason}", "    "
                )
            )
    if "investigated" in update:
        for item in update["investigated"]:
            result = item.result
            state = (
                "no analysis (dependency cause)"
                if result is None
                else ("succeeded" if result.succeeded else f"FAILED — {clip(result.error, 120)}")
            )
            print(indent(f"{item.repo_url or '—'} @ {item.commit or '—'}: {state}", "    "))
            findings = item.findings
            if findings:
                print(indent(f"answer: {clip(render_field(findings.answer), 140)}", "      "))
    if "diagnosis" in update:
        diagnosis = update["diagnosis"]
        print(
            indent(f"confidence   {diagnosis.confidence.value} — {diagnosis.confidence_rationale}")
        )
        print(indent(f"symptom      {clip(diagnosis.symptom.description, 200)}"))
        print(indent(f"cause        {clip(render_field(diagnosis.probable_cause), 200)}"))
        print(
            indent(
                f"location     {render_field(diagnosis.location.repo)} @ "
                f"{render_field(diagnosis.location.commit)} {diagnosis.location.paths}"
            )
        )
        for evidence in diagnosis.evidence:
            print(
                indent(
                    f"evidence [{evidence.kind.value}] {clip(evidence.description, 120)}", "    "
                )
            )
        for ruled in diagnosis.ruled_out:
            print(
                indent(f"ruled out: {clip(ruled.hypothesis, 70)} — {clip(ruled.why, 90)}", "    ")
            )
        for unknown in diagnosis.unknowns:
            print(
                indent(
                    f"unknown: {clip(unknown.question, 70)} — {clip(unknown.why_unresolved, 90)}",
                    "    ",
                )
            )
    if "dedup" in update:
        decision = update["dedup"]
        print(
            indent(f"matched {decision.matched} {decision.ticket_key or ''} — {decision.reasoning}")
        )
    if "draft" in update:
        print(indent(update["draft"].to_markdown(), "    "))
    if "verdict" in update:
        verdict = update["verdict"]
        print(indent(f"passes {verdict.passes}  missing {[s.value for s in verdict.missing]}"))
        if verdict.feedback:
            print(indent(f"feedback: {verdict.feedback}"))
    if "outcome" in update:
        print(indent(f"outcome {update['outcome'].value}  {update.get('ticket_key') or ''}"))
    if "postmortem" in update:
        print(indent(update["postmortem"], "    "))
    if "signal" in update:
        signal = update["signal"]
        print(indent(f"signal {signal.signal_id} → {signal.status.value}"))


def show_side_effects(deps: Deps, recorder: Recorder, spy: PromptSpy | None = None) -> None:
    if spy is not None and spy.calls:
        rule("model calls")
        for call in spy.calls:
            outcome = f"!! {clip(call.failed, 60)}" if call.failed else "ok"
            print(
                indent(
                    f"{call.seconds:5.1f}s {call.chars:>7d} chars  "
                    f"{call.tier:9s} {call.schema:24s} {outcome}"
                )
            )

    rule("Datadog calls")
    for call in recorder.calls:
        print(
            indent(
                f"{call.seconds:5.2f}s {call.items:>4d} items  "
                f"{call.endpoint:15s} {clip(call.query, 80)}"
            )
        )
    print(indent(f"{len(recorder.calls)} calls total"))

    rule("what would have been written")
    for issue in deps.jira.created:  # type: ignore[attr-defined]
        print(indent(f"JIRA {issue.project}: {issue.summary}  labels={list(issue.labels)}"))
    for comment in deps.jira.comments:  # type: ignore[attr-defined]
        print(indent(f"JIRA comment on {comment.key}: {clip(comment.body, 100)}"))
    for message in deps.slack.messages:  # type: ignore[attr-defined]
        thread = f" (thread {message.thread_ts})" if message.thread_ts else ""
        print(indent(f"SLACK {message.channel}{thread}: {clip(message.text, 120)}"))


async def run_collection_only(deps: Deps, state: dict[str, Any], forced: str | None) -> None:
    """The sweep on its own, for when there is no model proxy to reach.

    Everything except the class is a rule, so forcing the class costs the run
    nothing but honesty about which part a human supplied.
    """
    run = {"configurable": {DEPS_KEY: deps}}
    if forced:
        rule("node: classify_alert (forced)")
        classification = AlertClassification(
            alert_class=AlertClass(forced),
            reason=f"forced with --class {forced}: the triage tier was not called",
        )
        window = collection_window(state["alert"], deps.config.collection)
        print(indent(f"class  {classification.alert_class.value}  ({classification.reason})"))
        print(indent(f"window {window}"))
        state |= {"classification": classification, "window": window}
    else:
        update = await classify_alert(state, run)  # type: ignore[arg-type]
        show_update("classify_alert", update)
        state |= update

    started = time.monotonic()
    update = await collect(state, run)  # type: ignore[arg-type]
    show_update("collect", update)
    rule("stopped after the sweep")
    print(indent(f"{time.monotonic() - started:.1f}s wall clock"))
    print(
        indent(f"{DIM}--collect-only: qualify, analysis, ticket and post-mortem need models{RESET}")
    )


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="run_incident", description=__doc__)
    parser.add_argument("--monitor", type=int, help="Datadog monitor id")
    parser.add_argument("--find", help="monitor name to resolve to an id, instead of --monitor")
    parser.add_argument("--at", help="ISO-8601 moment to pick the firing nearest to")
    parser.add_argument("--hours", type=int, default=24, help="how far back to look")
    parser.add_argument("--group", help="substring of the firing group to pick")
    parser.add_argument("--team", help="override the team the scope resolution found")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        dest="mappings",
        help="service=repo_url[@commit], repeatable: seed the system map for this run",
    )
    parser.add_argument("--db", action="store_true", help="read the real system map from Postgres")
    parser.add_argument(
        "--local",
        action="store_true",
        help="run the analyses here, in a throwaway clone, instead of submitting Jobs",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="stop after the sweep: the collection half needs no model beyond the class",
    )
    parser.add_argument(
        "--class",
        dest="alert_class",
        choices=[item.value for item in AlertClass],
        help="force the class instead of asking the triage tier (needs --collect-only)",
    )
    parser.add_argument("--json", dest="json_out", help="write the final state to this file")
    parser.add_argument(
        "--prompts", action="store_true", help="print the full prompt sent to every tier"
    )
    parser.add_argument(
        "--prompt-chars",
        type=int,
        help="truncate each printed prompt to this many characters",
    )
    args = parser.parse_args(argv[1:])

    settings = get_settings()
    if not settings.dry_run:
        print("refusing to run: TRIAGE_DRY_RUN is off, this would file a real ticket")
        return 2
    if not (settings.datadog_api_key and settings.datadog_app_key):
        print("TRIAGE_DATADOG_API_KEY / TRIAGE_DATADOG_APP_KEY are unset — see datadog.env.example")
        return 2

    monitor_id = args.monitor
    if monitor_id is None:
        if not args.find:
            print("give either --monitor <id> or --find <monitor name>")
            return 2
        rule("finding the monitor")
        monitor_id = await find_monitor(
            settings.datadog_site, settings.datadog_api_key, settings.datadog_app_key, args.find
        )
        if monitor_id is None:
            return 1

    config = get_config()
    deps = build_deps(settings, config)
    client = DatadogRestClient(
        settings.datadog_site, settings.datadog_api_key, settings.datadog_app_key
    )
    recorder = Recorder(client)

    repo = deps.repo
    if args.db:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from triage.db.repo import SqlRepository

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        repo = SqlRepository(async_sessionmaker(engine, expire_on_commit=False))
    elif args.mappings:
        assert isinstance(repo, InMemoryRepository)
        seed_map(repo, args.mappings)

    if args.local:
        from triage.analysis.runner import LocalAnalysisRunner

        deps = Deps(**{**deps.__dict__, "runner": LocalAnalysisRunner(ANALYSIS_ENTRYPOINT)})

    spy = PromptSpy(deps.llm, show=args.prompts, limit=args.prompt_chars)
    deps = Deps(
        **{
            **deps.__dict__,
            "datadog": recorder,
            "repo": repo,
            "llm": spy,
            "github": build_github(settings),
        }
    )

    alert, transitions = await find_alert(
        recorder, monitor_id, parse_time(args.at), args.hours, args.group
    )
    if alert is None:
        print(f"no firing of monitor {monitor_id} in the last {args.hours}h")
        for other in transitions[:20]:
            print(indent(f"{other.fired_at.isoformat()} {other.status.value} {other.group}"))
        return 1

    if args.db:
        # The signals table deduplicates on the Datadog event id, which is the
        # poller's guarantee and this harness's problem: replaying one alert twice
        # is the normal thing to do here. The replay gets its own id and says so,
        # rather than the run dying on a unique constraint.
        stamp = datetime.now(UTC).strftime("%H%M%S")
        alert = alert.model_copy(update={"event_id": f"{alert.event_id}:replay-{stamp}"})
        print(indent(f"{DIM}--db: stored as a replay signal, event id suffixed{RESET}"))

    show_alert(alert, transitions)
    show_gate(alert, deps, recovery_of(alert, transitions))

    routing = resolve(config, alert)
    state: dict[str, Any] = {
        "alert": alert,
        "team": args.team or routing.team or "platform",
        "service": alert.scope.workload or "unknown",
    }

    if args.collect_only:
        await run_collection_only(deps, state, args.alert_class)
        show_side_effects(deps, recorder, spy)
        await client.aclose()
        return 0

    started = time.monotonic()
    final: dict[str, Any] = {}
    async for namespace, update in (
        build_graph()
        .compile()
        .astream(
            state, config={"configurable": {DEPS_KEY: deps}}, stream_mode="updates", subgraphs=True
        )
    ):
        for node, payload in update.items():
            if not isinstance(payload, dict):
                continue
            final.update(payload)
            if node in SUBGRAPHS and not namespace:
                continue
            label = ":".join([*(part.split(":")[0] for part in namespace), node])
            show_update(label, payload)

    rule("done")
    print(indent(f"{time.monotonic() - started:.1f}s wall clock"))
    show_side_effects(deps, recorder, spy)

    if args.json_out:
        from pydantic import BaseModel

        def encode(value: Any) -> Any:
            return value.model_dump(mode="json") if isinstance(value, BaseModel) else str(value)

        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(final, handle, indent=2, default=encode)
        print(indent(f"final state → {args.json_out}"))

    await client.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
