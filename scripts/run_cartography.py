"""Run the cartography graph over config.yaml and print the map it would persist.

    make run-cartography
    make run-cartography REPOS="github.com/org/payments-api@9f2c1ab"
    make run-cartography REPOS="--merge github.com/org/payments-api@9f2c1ab"
    make run-cartography REPOS="--full"
    make run-cartography LOCAL=1

Needs no database and no credentials: with ``TRIAGE_DRY_RUN=1`` the repository
is in-memory and Slack is a recording fake. By default the analysis runner is
the dry-run one, which submits nothing — so the run shows the wiring and the
routing, and every repository comes back unsummarised.

``LOCAL=1`` swaps in :class:`LocalAnalysisRunner`, which clones each repository
here and runs the entrypoint against the real model tier: the same trade
``make run-fixture`` makes, and the only way to see the merge on real summaries
without a cluster. It needs network, a clone per repository, and spends money.

``--merge`` sends one repository in as a merge event, which is the incremental
path (ADR-0015). In dry run no GitHub comparison is made, so it always decides to
re-summarise and says why — the carry-forward branch is exercised by the tests,
not here.
"""

import asyncio
import sys
import textwrap
from dataclasses import replace

from triage.analysis.runner import LocalAnalysisRunner
from triage.config import get_config, get_settings
from triage.graphs.cartography import graph
from triage.integrations.base import FakeSlackClient
from triage.runtime import DEPS_KEY, build_deps

ENTRYPOINT = [sys.executable, "-m", "triage.analysis.entrypoint"]


def rule(title: str) -> None:
    print(f"\n\033[1m── {title} {'─' * max(0, 66 - len(title))}\033[0m")


def indent(text: str) -> str:
    return textwrap.indent(text.rstrip(), "  ")


def parse_refs(argv: list[str]) -> list[dict[str, str | None]]:
    """``url`` or ``url@commit``, space separated. Empty means every declared repo."""
    refs: list[dict[str, str | None]] = []
    for arg in argv:
        for token in arg.split():
            url, _, commit = token.partition("@")
            refs.append({"url": url, "commit": commit or None})
    return refs


async def main(argv: list[str]) -> int:
    settings = get_settings()
    if not settings.dry_run:
        print("refusing to run: TRIAGE_DRY_RUN is off, this would launch real analysis Jobs")
        return 2

    config = get_config()
    deps = build_deps(settings, config)
    flags = {arg for arg in argv[1:] if arg.startswith("--")}
    arguments = [arg for arg in argv[1:] if not arg.startswith("--")]
    if "--local" in flags:
        deps = replace(deps, runner=LocalAnalysisRunner(ENTRYPOINT))
    refs = parse_refs(arguments)

    if "--merge" in flags:
        if len(refs) != 1 or not refs[0]["commit"]:
            print("--merge takes exactly one url@commit", file=sys.stderr)
            return 2
        request = {"merge_event": {"repo_url": refs[0]["url"], "commit": refs[0]["commit"]}}
    else:
        request = {"repos": refs}
    request["full"] = "--full" in flags

    rule("input")
    print(
        indent(
            f"{'merge event' if '--merge' in flags else 'scheduled pass'}"
            f"{', forced full' if request['full'] else ''}"
        )
    )
    for ref in refs or [{"url": repo.url, "commit": None} for repo in config.repos]:
        print(indent(f"{ref['url']} @ {ref['commit'] or 'HEAD'}"))

    state = await graph.ainvoke(request, config={"configurable": {DEPS_KEY: deps}})

    system_map = state.get("system_map")
    rule("system map that would be persisted")
    print(indent(f"rows written: {state.get('entries_written', 0)}"))
    for service in system_map.services if system_map else []:
        print(indent(f"service {service.name} — team {service.team or 'UNOWNED'}"))
        print(indent(f"  repo {service.repo_url} @ {service.source_commit or 'unrecorded'}"))
        print(indent(f"  terraform resources: {len(service.terraform_resources)}"))
    for module in system_map.terraform_modules if system_map else []:
        print(indent(f"module {module.name} — team {module.team or 'UNOWNED'}"))
        print(indent(f"  resources: {len(module.resources)}"))

    carried = state.get("carried_forward", [])
    if carried:
        rule("not re-summarised")
        for entry in carried:
            print(indent(f"{entry.repo_url} → {entry.commit} — {entry.reason}"))

    failures = state.get("failures", [])
    if failures:
        rule("not summarised")
        for failure in failures:
            print(indent(f"{failure.repo_url} — {failure.reason}"))

    assert isinstance(deps.slack, FakeSlackClient)
    for message in deps.slack.messages:
        rule(f"slack message that would be posted to {message.channel}")
        print(indent(message.text))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
