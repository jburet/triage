"""Choosing what to summarise, and summarising it (architecture §2.5).

The analyses run one after another rather than fanned out: each one is a clone
and a sandboxed Job with a fifteen-minute budget (ADR-0009), and the Platform's
task queue is where concurrency is decided (ADR-0001). The weekly full pass runs
when nobody is waiting, so wall-clock here buys nothing worth the contention.

A repository that fails to summarise is recorded and skipped, never raised: one
unreachable repository must not cost the map every other repository in the run.
"""

from langchain_core.runnables import RunnableConfig

from triage.analysis.summaries import summarize_repo, summarize_terraform
from triage.config import Config, RepoKind
from triage.graphs.state import (
    CartographyState,
    MergeEvent,
    RepoRef,
    RepoTarget,
    Summarised,
    SummaryFailure,
)
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.analysis import AnalysisResult

DEFAULT_REF = "HEAD"


def _target(config: Config, ref: RepoRef) -> RepoTarget | None:
    declared = next((repo for repo in config.repos if repo.url == ref.url), None)
    if declared is None:
        return None
    return RepoTarget(
        url=declared.url,
        kind=declared.kind,
        team=declared.team if config.declares_team(declared.team) else None,
        commit=ref.commit,
    )


def _requested(state: CartographyState, config: Config) -> list[RepoRef]:
    """What this run was asked for, validated.

    The input arrives as JSON when the Platform creates the run — a merge webhook
    or a cron — so the entry node validates it rather than trusting the state to
    already hold models. An empty ask means every repository config declares.
    """
    event = state.get("merge_event")
    if event is not None:
        merge = MergeEvent.model_validate(event)
        return [RepoRef(url=merge.repo_url, commit=merge.commit)]
    requested = state.get("repos")
    if requested:
        return [RepoRef.model_validate(ref) for ref in requested]
    return [RepoRef(url=repo.url) for repo in config.repos]


async def select_targets(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """Join the repositories this run was asked for with what ``config.yaml`` declares.

    A repository nobody declared is not summarised at all: ownership, kind and
    the Slack channel to complain to all come from config, and a repository
    without them cannot be placed on the map.
    """
    deps = deps_from_runnable_config(config)
    targets: list[RepoTarget] = []
    failures: list[SummaryFailure] = []

    for ref in _requested(state, deps.config):
        target = _target(deps.config, ref)
        if target is None:
            failures.append(
                SummaryFailure(
                    repo_url=ref.url,
                    reason="not declared in config.yaml, so it has no owner or kind",
                )
            )
            continue
        targets.append(target)

    return {"targets": targets, "failures": failures}


async def _summarise_one(deps: Deps, target: RepoTarget) -> AnalysisResult:
    commit = target.commit or DEFAULT_REF
    if target.kind is RepoKind.TERRAFORM:
        return await summarize_terraform(deps.runner, repo_url=target.url, commit=commit)
    return await summarize_repo(deps.runner, repo_url=target.url, commit=commit)


async def summarize(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """Summarise every target, keeping the successes and recording the rest."""
    deps = deps_from_runnable_config(config)
    failures = list(state.get("failures", []))
    summaries: list[Summarised] = []

    for target in state.get("targets", []):
        result = await _summarise_one(deps, target)
        if not result.succeeded or result.result is None:
            failures.append(
                SummaryFailure(repo_url=target.url, reason=result.error or "no result returned")
            )
            continue
        summaries.append(Summarised(target=target, summary=result.result))

    return {"summaries": summaries, "failures": failures}
