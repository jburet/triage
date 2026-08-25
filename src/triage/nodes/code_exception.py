"""Opening and settling one error group (M8 4.5, 4.6).

F1 opens a Slack thread per *incident* because an alert cycle is a thing that
starts and stops. An error group does not: it is the same defect, seen again an
hour later and again a week after that, and ADR-0025's escalation guarantees it
will be reported more than once. So the thread belongs to the group and lives on
its row, which is what makes the fourth report a reply under the first rather
than a fifth conversation in the channel — the failure ADR-0023 says to watch
for, arriving one report at a time instead of all at once.

Settling is the group's own row too. ``reported`` is terminal for this pass and
not for the group: the next tick derives the same key from the same fields, finds
the row, and the gate reads the cooldown and the escalation off it.
"""

from datetime import UTC, datetime

import structlog
from langchain_core.runnables import RunnableConfig

from triage.config import PLATFORM_TEAM
from triage.errors.versions import loudest_service
from triage.graphs.state import CodeExceptionState
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.common import Feature
from triage.schemas.errors import ErrorGroup, ErrorGroupStatus
from triage.schemas.ticket import PipelineOutcome

log = structlog.get_logger(__name__)

SETTLED_STATUS: dict[PipelineOutcome, ErrorGroupStatus] = {
    PipelineOutcome.TICKET_CREATED: ErrorGroupStatus.REPORTED,
    PipelineOutcome.TICKET_UPDATED: ErrorGroupStatus.REPORTED,
    PipelineOutcome.REPORT_POSTED: ErrorGroupStatus.REPORTED,
}
"""How a pass ended, as the group records it.

Anything absent is a run that concluded nothing a human was shown, and leaves the
group ``open`` — the next tick may take it up again, throttled by the cooldown
the tick that selected it already stamped on."""


def _channel(deps: Deps, group: ErrorGroup) -> str:
    team = group.team or PLATFORM_TEAM
    return deps.config.team(team).slack_channel


def _opening(group: ErrorGroup) -> str:
    tenants = len(group.services)
    name = group.error_type.rsplit(".", 1)[-1]
    if group.thread_ts is None:
        return (
            f":mag: Investigating `{name}` in *{group.repository}* — "
            f"{group.occurrences:,} occurrences in {tenants} "
            f"tenant{'' if tenants == 1 else 's'}, raised at {group.source_location}.\n"
            f"Triage is reading the code and will report here."
        )
    return (
        f":repeat: `{name}` in *{group.repository}* is back: {group.occurrences:,} more "
        f"occurrences, {group.cumulative_occurrences:,} in total. This is report "
        f"{max(group.analysis_count, 1)}."
    )


async def open_group(
    state: CodeExceptionState, config: RunnableConfig | None = None
) -> CodeExceptionState:
    """Mark the group as being analysed and find, or open, its one thread."""
    deps = deps_from_runnable_config(config)
    group = state["group"]
    thread_ts = await deps.slack.post(
        channel=_channel(deps, group),
        text=_opening(group),
        thread_ts=group.thread_ts,
    )
    stored = await deps.repo.upsert_error_group(
        group.model_copy(
            update={
                "status": ErrorGroupStatus.ANALYSING,
                "thread_ts": group.thread_ts or thread_ts,
            }
        )
    )
    return {
        "group": stored,
        "thread_ts": stored.thread_ts,
        "feature": Feature.F2,
        "service": loudest_service(stored),
        "team": stored.team or PLATFORM_TEAM,
    }


async def settle_group(
    state: CodeExceptionState, config: RunnableConfig | None = None
) -> CodeExceptionState:
    """Record how the pass ended, on the group's own row (behaviour 4.6)."""
    deps = deps_from_runnable_config(config)
    group = state.get("group")
    if group is None:
        return {}
    outcome = state.get("outcome")
    status = ErrorGroupStatus.OPEN
    if outcome is not None:
        status = SETTLED_STATUS.get(outcome, status)
    settled = await deps.repo.upsert_error_group(
        group.model_copy(
            update={
                "status": status,
                "last_analysed_at": group.last_analysed_at or datetime.now(UTC),
            }
        )
    )
    log.info("error_group_settled", group=settled.key, status=status.value)
    return {"group": settled}
