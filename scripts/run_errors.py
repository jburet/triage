"""Tick the code-exception poller by hand, the way the hourly cron will (ADR-0025).

    make run-errors                          # one tick, in-memory, nothing persisted
    make run-errors ARGS="--db"              # one tick against Postgres, watermark and all
    make run-errors ARGS="--hours 24"        # a day, to see what a backlog sweep would find
    make run-errors ARGS="--analyse"         # then drive every gated group end to end

The sibling of ``scripts/run_poller``. F2 needs no Platform cron to be developed:
this is the schedule made manual, and `deploy/platform/cron-error-poller.yaml`
will run the same `error_poller` graph on the same hourly period.

**What it touches.** Datadog: read-only, one Error Tracking search per configured
track. Postgres: only with ``--db``, and without it every tick starts from an
empty watermark and re-reads the configured lookback. No model call anywhere on
the tick itself — it classifies and counts, and nothing it decides costs money.

``--analyse`` is the part that does. It runs the ``code_exception`` graph over
every group the gate took up, which is roughly one ``analysis`` call to qualify,
one ``diagnosis`` call to synthesise and one ``triage`` call to deduplicate, per
group — capped by ``errors.max_groups_per_tick``. GitHub is read for real, so a
version a repository claims resolves to a real commit; Jira and Slack stay
recording fakes and what would have been posted is printed instead. The analysis
runner is the dry-run one: no repository is cloned and no code is read, so what
this shows is the *shape* of a report and not its findings.
"""

import argparse
import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from triage.config import get_config, get_settings
from triage.graphs.code_exception import run_code_exception
from triage.graphs.error_poller import graph
from triage.integrations.datadog import DatadogRestClient
from triage.report import NAMED_SERVICES
from triage.runtime import DEPS_KEY, Deps, build_deps, build_github
from triage.schemas.errors import ErrorGroup

ANALYSIS_ENTRYPOINT = [sys.executable, "-m", "triage.analysis.entrypoint"]
"""Run an analysis in this interpreter, as ``run_incident --local`` does."""

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true", help="read and write Postgres")
    parser.add_argument(
        "--local",
        action="store_true",
        help="run the analyses here, in a throwaway clone, instead of submitting Jobs",
    )
    parser.add_argument(
        "--analyse",
        action="store_true",
        help="run the code_exception graph over every group the gate took up (spends money)",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="read this many hours back, ignoring the watermark and the catch-up limit",
    )
    return parser.parse_args(argv[1:])


def _tenants(group: ErrorGroup) -> str:
    """Worst first, and the tail counted — one group spanned 66 tenants."""
    ordered = sorted(group.services.items(), key=lambda item: (-item[1], item[0]))
    named, tail = ordered[:NAMED_SERVICES], ordered[NAMED_SERVICES:]
    shown = ", ".join(f"{name} {count:,d}" for name, count in named)
    if not tail:
        return shown
    return f"{shown}, and {len(tail)} more totalling {sum(c for _, c in tail):,d}"


def report(state: dict) -> None:
    window = state.get("window")
    print(f"\n{BOLD}tick {state.get('now')}{RESET}")
    if window is not None:
        print(f"  {DIM}window{RESET}     {window}")
    if state.get("query"):
        print(f"  {DIM}query{RESET}      {state['query']}")
    for track, count in (state.get("issues_seen") or {}).items():
        print(f"  {DIM}{track}{RESET}      {count} issues occurring")
    print(f"  {'unchanged':<12} {state.get('unchanged', 0)}")
    print(f"  {'seen again':<12} {state.get('seen_again', 0)} known groups had their total moved")
    for label, key in (("new", "new"), ("regressed", "regressed")):
        for issue in state.get(key, []) or []:
            print(
                f"  {label:<12} {issue.service:<26} {issue.occurrences:>8,d}  "
                f"{(issue.error_type or '?').rsplit('.', 1)[-1]} at {issue.source_location}"
            )
    for skipped in state.get("skipped", []) or []:
        print(f"  {'skipped':<12} {skipped.issue_id[:8]}  {skipped.reason}")
    for failure in state.get("failures", []) or []:
        print(f"  {'failed':<12} {failure}")
    if state.get("skipped_span"):
        print(f"  {'behind':<12} {state['skipped_span']}")
    _groups(state)


def _groups(state: dict) -> None:
    """What the tick made of the issues it looked at. Held back is not nothing found."""
    decisions = state.get("decisions") or []
    if not decisions:
        return
    print(f"  {DIM}groups{RESET}     {len(state.get('groups') or [])}")
    for decision in decisions:
        group = decision.group
        services = _tenants(group)
        print(
            f"  {decision.outcome.value:<12} {group.occurrences:>8,d}  "
            f"{group.error_type.rsplit('.', 1)[-1]} at {group.source_location}"
        )
        print(f"  {'':<12} {DIM}{services}{RESET}")
        print(f"  {'':<12} {DIM}{decision.reason}{RESET}")


async def main(argv: list[str]) -> int:
    options = parse(argv)
    settings = get_settings()
    if not (settings.datadog_api_key and settings.datadog_app_key):
        print("TRIAGE_DATADOG_API_KEY / TRIAGE_DATADOG_APP_KEY are unset — see datadog.env.example")
        return 2

    deps = build_deps(settings, get_config())
    deps = replace(
        deps,
        datadog=DatadogRestClient(
            settings.datadog_site, settings.datadog_api_key, settings.datadog_app_key
        ),
    )
    if options.db:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from triage.db.repo import SqlRepository

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        deps = replace(deps, repo=SqlRepository(async_sessionmaker(engine, expire_on_commit=False)))
    else:
        print(f"{DIM}in-memory: no watermark survives this process (pass --db){RESET}")

    if options.local:
        from triage.analysis.runner import LocalAnalysisRunner

        deps = replace(deps, runner=LocalAnalysisRunner(ANALYSIS_ENTRYPOINT))

    tick: dict[str, object] = {}
    if options.hours is not None:
        tick["since"] = datetime.now(UTC) - timedelta(hours=options.hours)

    state = await graph.ainvoke(tick, config={"configurable": {DEPS_KEY: deps}})
    report(state)
    if options.analyse:
        await analyse(deps, state.get("analysing") or [])
    return 0


async def analyse(deps: Deps, groups: list[ErrorGroup]) -> None:
    """Drive each gated group end to end, and print what Slack would have received."""
    if not groups:
        print(f"\n{DIM}no group cleared the gate this tick, so there is nothing to analyse{RESET}")
        return
    deps = replace(deps, github=build_github(get_settings()))
    for group in groups:
        print(f"\n{BOLD}── {group.key} {'─' * max(0, 60 - len(group.key))}{RESET}")
        before = len(deps.slack.messages) if hasattr(deps.slack, "messages") else 0
        try:
            final = await run_code_exception({"group": group}, deps)
        except Exception as exc:
            print(f"  {DIM}failed{RESET}     {type(exc).__name__}: {exc}")
            continue
        diagnosis = final.get("diagnosis")
        if diagnosis is not None:
            print(f"  {DIM}confidence{RESET} {diagnosis.confidence.value}")
        print(f"  {DIM}outcome{RESET}    {final.get('outcome')}")
        for message in getattr(deps.slack, "messages", [])[before:]:
            print(indent(message.text))


def indent(text: str) -> str:
    return "\n".join(f"  | {line}" for line in str(text).splitlines())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
