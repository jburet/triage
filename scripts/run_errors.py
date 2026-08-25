"""Tick the code-exception poller by hand, the way the hourly cron will (ADR-0025).

    make run-errors                          # one tick, in-memory, nothing persisted
    make run-errors ARGS="--db"              # one tick against Postgres, watermark and all
    make run-errors ARGS="--hours 168"       # a week, to see what a backlog sweep would find

The sibling of ``scripts/run_poller``. F2 needs no Platform cron to be developed:
this is the schedule made manual, and `deploy/platform/cron-error-poller.yaml`
will run the same `error_poller` graph on the same hourly period.

**What it touches.** Datadog: read-only, one Error Tracking search per configured
track. Postgres: only with ``--db``, and without it every tick starts from an
empty watermark and re-reads the configured lookback. No model call anywhere on
this path — Phase 1 classifies and counts, and nothing it decides costs money.
"""

import argparse
import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from triage.config import get_config, get_settings
from triage.graphs.error_poller import graph
from triage.integrations.datadog import DatadogRestClient
from triage.runtime import DEPS_KEY, build_deps

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true", help="read and write Postgres")
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="override the window by pretending the watermark is this many hours old",
    )
    return parser.parse_args(argv[1:])


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
        services = ", ".join(f"{name} {count:,d}" for name, count in group.services.items())
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

    if options.hours is not None:
        from triage.nodes.poll_errors import OVERLAP, POLLER_NAME

        await deps.repo.set_watermark(
            POLLER_NAME, datetime.now(UTC) - timedelta(hours=options.hours) + OVERLAP
        )

    state = await graph.ainvoke({}, config={"configurable": {DEPS_KEY: deps}})
    report(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
