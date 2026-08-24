"""Tick the alert poller by hand, the way the Platform cron would (ADR-0017, ADR-0018).

    make run-poller                        # one tick, in-memory, nothing persisted
    make run-poller ARGS="--db"            # one tick against Postgres, watermark and all
    make run-poller ARGS="--db --every 60" # what the cron does, until interrupted

This is the manual entry point the cron replaces — the schedule, not the code:
`deploy/platform/cron-alert-poller.yaml` runs the same `alert_poller` graph on the
same one-minute period, and `scripts/apply_cron.py` creates it.

**What it touches.** Datadog: read-only, one event search per tick. Postgres: only
with `--db`, and without it every tick starts from an empty watermark and re-reads
the same two minutes. What it can *cost*: a signal that passes the persistence
gate is launched, and with no Platform configured that launch runs the whole
incident graph in this process — several model calls and, once there is an image,
an analysis Job. A tick over a quiet window is free; a tick that opens the gate is
not.
"""

import argparse
import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime

from triage.config import get_config, get_settings
from triage.graphs.poller import graph
from triage.integrations.datadog import DatadogRestClient
from triage.runtime import DEPS_KEY, build_deps
from triage.schemas.signal import SignalStatus

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true", help="read and write Postgres")
    parser.add_argument(
        "--every",
        type=float,
        default=None,
        metavar="SECONDS",
        help="keep ticking on this period, as the cron does, until interrupted",
    )
    return parser.parse_args(argv[1:])


def report(state: dict) -> None:
    now = state.get("now") or datetime.now(UTC)
    print(f"\n{BOLD}tick {now.isoformat()}{RESET} — {state.get('events_seen', 0)} alert events")
    if state.get("skipped_span"):
        print(f"  {DIM}skipped{RESET}   {state['skipped_span']}")
    for key, label in (
        ("created", "waiting"),
        ("launched", "launched"),
        ("unmapped", "unmapped"),
        ("out_of_scope", "out of scope"),
    ):
        for signal_id in state.get(key, []) or []:
            print(f"  {label:<12} {signal_id}")
    for signal in state.get("recovered", []) or []:
        seconds = signal.duration_seconds or 0
        print(f"  {'recovered':<12} {signal.signal_id} after {seconds / 60:.1f} minutes")
    for cycle in state.get("flapping", []) or []:
        print(f"  {'flapping':<12} {cycle}")


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

    where = "the Platform" if deps.platform is not None else "this process"
    print(f"{DIM}a signal past its gate is run on {where}{RESET}")

    while True:
        state = await graph.ainvoke({}, config={"configurable": {DEPS_KEY: deps}})
        report(state)
        if options.every is None:
            break
        await asyncio.sleep(options.every)

    if options.db:
        open_signals = [
            signal
            for signal in await deps.repo.open_signals()
            if signal.status is SignalStatus.WAITING
        ]
        print(f"\n{len(open_signals)} signals waiting for their gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
