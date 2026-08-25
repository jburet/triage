"""What one tick does with the issues it decided to look at (ADR-0025, ADR-0026).

Three steps, none of which asks a model anything. The issues are collapsed into
one group per defect, using the repository the mono-tenancy rule resolves rather
than the tenant Datadog grouped by. Every group is persisted with its count,
including — especially — the ones nothing will be done about, because the
cumulative total the escalation reads is a number no single tick can see. Then
the volume gate says which of them this tick takes up.

Everything the gate refused is reported rather than dropped. A tick that held
back four groups, deferred two and analysed none has to look different from a
tick that found nothing at all, and over a quiet hour those are the same
observation until the numbers are said out loud.

Taking a group up is written down here rather than left to the report. A run
that dies after a group was selected must not have that group re-selected every
tick for ever, and the ordinal a later report needs — "this is the fourth time"
— is the count of times it was taken up.
"""

from datetime import UTC, datetime

import structlog
from langchain_core.runnables import RunnableConfig

from triage.config import RepoKind
from triage.errors.gate import GateDecision, GateOutcome, gate, held_back
from triage.errors.grouping import ServiceRepository, group_issues
from triage.graphs.state import ErrorPollerState
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.errors import ErrorGroup, ErrorGroupStatus
from triage.scope import deployed_repo

log = structlog.get_logger(__name__)


async def group_error_issues(
    state: ErrorPollerState, config: RunnableConfig | None = None
) -> ErrorPollerState:
    """Collapse, persist, gate — and say what was held back and what was deferred."""
    deps = deps_from_runnable_config(config)
    now = state.get("now") or datetime.now(UTC)
    regressed = state.get("regressed", [])
    issues = [*state.get("new", []), *regressed]
    result: ErrorPollerState = {
        "groups": [],
        "decisions": [],
        "analysing": [],
        "held_back": 0,
        "deferred": [],
        "unmapped": [],
    }
    if not issues:
        return result

    resolved = await _repositories(deps, sorted({issue.service for issue in issues}))
    groups = group_issues(issues, resolved.get, regressed={issue.issue_id for issue in regressed})
    stored = [await deps.repo.upsert_error_group(group) for group in groups]
    decisions = gate(stored, deps.config.errors, now)

    result["groups"] = stored
    result["decisions"] = decisions
    result["analysing"] = [
        await deps.repo.upsert_error_group(_taken_up(decision.group, now))
        for decision in decisions
        if decision.analysed
    ]
    result["held_back"] = held_back(decisions)
    result["deferred"] = _keys(decisions, GateOutcome.DEFERRED)
    result["unmapped"] = _keys(decisions, GateOutcome.UNMAPPED)
    log.info(
        "error_groups",
        groups=len(stored),
        analysing=len(result["analysing"]),
        held_back=result["held_back"],
        deferred=len(result["deferred"]),
        unmapped=len(result["unmapped"]),
    )
    return result


def _keys(decisions: list[GateDecision], outcome: GateOutcome) -> list[str]:
    return [decision.group.key for decision in decisions if decision.outcome is outcome]


def _taken_up(group: ErrorGroup, now: datetime) -> ErrorGroup:
    """The group as the tick leaves it: selected, counted, and its escalation moved on.

    ``analysed_at_cumulative`` moves to the current total so the next interval is
    counted from here — leaving it at zero would make every subsequent tick cross
    the threshold again and report the same defect for ever.
    """
    return group.model_copy(
        update={
            "status": ErrorGroupStatus.ANALYSING,
            "analysis_count": group.analysis_count + 1,
            "analysed_at_cumulative": group.cumulative_occurrences,
            "last_analysed_at": now,
        }
    )


async def _repositories(deps: Deps, services: list[str]) -> dict[str, ServiceRepository]:
    """Which repository each service runs, by the ladder ``scope`` already owns.

    A service nothing answers for is simply absent from the map: the grouping
    rule turns that into a group of its own, reported as a gap rather than
    guessed at.
    """
    resolved: dict[str, ServiceRepository] = {}
    for service in services:
        deployment = await deployed_repo(deps.config, deps.repo, service, RepoKind.APPLICATION)
        if deployment.repo_url is None:
            continue
        declared = deps.config.repo_by_url(deployment.repo_url)
        resolved[service] = ServiceRepository(
            repository=declared.name if declared else deployment.repo_url.rsplit("/", 1)[-1],
            repo_url=deployment.repo_url,
            team=declared.team if declared else None,
        )
    return resolved
