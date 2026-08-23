"""Choosing what to summarise, and summarising it (architecture §2.5).

The analyses run one after another rather than fanned out: each one is a clone
and a sandboxed Job with a fifteen-minute budget (ADR-0009), and the Platform's
task queue is where concurrency is decided (ADR-0001). The weekly full pass runs
when nobody is waiting, so wall-clock here buys nothing worth the contention.

A repository that fails to summarise is recorded and skipped, never raised: one
unreachable repository must not cost the map every other repository in the run.
"""

import structlog
from langchain_core.runnables import RunnableConfig

from triage.analysis.invalidation import invalidation_for
from triage.analysis.summaries import summarize_repo, summarize_terraform
from triage.config import Config, RepoKind
from triage.graphs.state import (
    CarriedForward,
    CartographyState,
    MergeEvent,
    RepoRef,
    RepoTarget,
    Summarised,
    SummaryFailure,
)
from triage.integrations.github import GitHubError
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.analysis import AnalysisResult

log = structlog.get_logger(__name__)

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


def _merge_event(state: CartographyState) -> MergeEvent | None:
    event = state.get("merge_event")
    return MergeEvent.model_validate(event) if event is not None else None


def _requested(state: CartographyState, config: Config) -> list[RepoRef]:
    """What this run was asked for, validated.

    The input arrives as JSON when the Platform creates the run — a merge webhook
    or a cron — so the entry node validates it rather than trusting the state to
    already hold models. An empty ask means every repository config declares.
    """
    merge = _merge_event(state)
    if merge is not None:
        return [RepoRef(url=merge.repo_url, commit=merge.commit)]
    requested = state.get("repos")
    if requested:
        return [RepoRef.model_validate(ref) for ref in requested]
    return [RepoRef(url=repo.url) for repo in config.repos]


async def _still_accurate(deps: Deps, target: RepoTarget) -> CarriedForward | None:
    """Whether this merge can be answered without re-summarising (ADR-0006, ADR-0015).

    Every uncertainty resolves towards re-summarising. A repository with no prior
    summary, one whose recorded commit is unknown, and a comparison GitHub would
    not answer are all *full summary*: the cost of an unnecessary analysis is one
    tier call, and the cost of a wrongly skipped one is a map that is confidently
    wrong until the weekly pass.
    """
    if target.commit is None:
        return None
    base = await deps.repo.last_summarised_commit(target.url)
    if base is None:
        return None
    if base == target.commit:
        return CarriedForward(
            repo_url=target.url,
            commit=target.commit,
            reason="already summarised at this commit",
        )
    try:
        changed = await deps.github.changed_paths(target.url, base=base, head=target.commit)
    except GitHubError as exc:
        log.warning("cartography_comparison_failed", repo=target.url, error=str(exc))
        return None

    decision = invalidation_for(changed, target.kind)
    if decision.stale:
        return None
    return CarriedForward(repo_url=target.url, commit=target.commit, reason=decision.reason)


async def select_targets(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """Join the repositories this run was asked for with what ``config.yaml`` declares.

    A repository nobody declared is not summarised at all: ownership, kind and
    the Slack channel to complain to all come from config, and a repository
    without them cannot be placed on the map.

    A merge event is also judged here against what the map already reflects, so
    that a merge which cannot have changed a summary costs no analysis at all
    (ADR-0015). ``full`` skips that judgement: it is the weekly pass, whose whole
    purpose is to re-derive what the incremental runs assumed.
    """
    deps = deps_from_runnable_config(config)
    incremental = _merge_event(state) is not None and not state.get("full")
    targets: list[RepoTarget] = []
    carried: list[CarriedForward] = []
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
        accurate = await _still_accurate(deps, target) if incremental else None
        if accurate is not None:
            carried.append(accurate)
            continue
        targets.append(target)

    return {"targets": targets, "carried_forward": carried, "failures": failures}


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
