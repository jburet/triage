"""Run the service-mapping graph over real cluster telemetry and print what it found.

    make run-mapping ARGS="plt-hcl-software-uat"
    make run-mapping ARGS="--days 14 plt-merck-qa plt-hcl-software-uat"
    make run-mapping ARGS="--db plt-hcl-software-uat"
    make run-mapping                       # every service seen recently; needs --db

Read-only and free: Datadog events only, no model call anywhere on this path.
Named services are derived; with none, the pass covers every service that has
alerted recently, which needs ``--db`` to have anything to enumerate.

``--db`` persists the mappings to Postgres, which is what an incident run in
another process reads. Without it the derivation is printed and thrown away.

The report printed at the end is the one the pass posts to the platform channel,
rendered by the same function: a local run and a scheduled one must not disagree
about how much of the map is observed.
"""

import asyncio
import sys
from dataclasses import replace

from triage.config import get_config, get_settings
from triage.graphs.mapping import graph
from triage.integrations.datadog import DatadogRestClient
from triage.mapping.report import render
from triage.runtime import DEPS_KEY, build_deps, build_github
from triage.schemas.common import render as render_field
from triage.schemas.system_map import MappingOutcome

BOLD, RESET = "\033[1m", "\033[0m"


def rule(title: str) -> None:
    print(f"\n{BOLD}── {title} {'─' * max(0, 66 - len(title))}{RESET}")


async def main(argv: list[str]) -> int:
    settings = get_settings()
    if not (settings.datadog_api_key and settings.datadog_app_key):
        print("TRIAGE_DATADOG_API_KEY / TRIAGE_DATADOG_APP_KEY are unset — see datadog.env.example")
        return 2

    flags = {arg for arg in argv[1:] if arg.startswith("--")}
    arguments = [arg for arg in argv[1:] if not arg.startswith("--")]
    days = 7
    if "--days" in flags:
        index = argv.index("--days")
        days = int(argv[index + 1])
        arguments = [arg for arg in arguments if arg != argv[index + 1]]

    config = get_config()
    deps = build_deps(settings, config)
    deps = replace(
        deps,
        datadog=DatadogRestClient(
            settings.datadog_site, settings.datadog_api_key, settings.datadog_app_key
        ),
        github=build_github(settings),
    )
    if "--db" in flags:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from triage.db.repo import SqlRepository

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        deps = replace(deps, repo=SqlRepository(async_sessionmaker(engine, expire_on_commit=False)))

    state = await graph.ainvoke(
        {"services": arguments, "lookback_days": days},
        config={"configurable": {DEPS_KEY: deps}},
    )

    rule(f"services derived over the last {days} days")
    for service in state.get("targets", []):
        print(f"  {service}")

    for outcome in MappingOutcome:
        lines = [item for item in state.get("derivations", []) if item.outcome is outcome]
        if not lines:
            continue
        rule(outcome.value.replace("_", " "))
        for item in lines:
            print(f"  {item.service} — {item.reason}")
            entry = item.entry
            if entry is not None:
                print(f"    repository {entry.repository} ({entry.repo_url or 'undeclared'})")
                print(f"    image      {entry.image or 'none observed'}")
                origin = entry.commit_source.value if entry.commit_source else "nothing"
                stood = f" as at {entry.commit_read_at.isoformat()}" if entry.commit_read_at else ""
                print(f"    commit     {render_field(entry.deployed_commit)}")
                print(f"    from       {origin}{stood}")
                print(f"    iac        {entry.iac_repo or 'none'} — source {entry.source.value}")

    report = state.get("report")
    if report is not None:
        rule("report")
        print(render(report))

    rule("written")
    print(f"  {state.get('entries_written', 0)} workload rows")
    if "--db" not in flags:
        print("  (in-memory: pass --db to persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
