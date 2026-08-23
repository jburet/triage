"""Persistence nodes and the shared terminal bookkeeping."""

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from triage.graphs.state import TicketPipelineState
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.ticket import PipelineOutcome


async def record_diagnosis(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Store the diagnosis before anything else can fail.

    A diagnosis that produced no ticket because Jira was down is still the
    expensive part of the run, and is still evidence for the next occurrence.
    """
    deps = deps_from_runnable_config(config)
    diagnosis_id = await deps.repo.save_diagnosis(state["diagnosis"])
    return {
        "diagnosis_id": diagnosis_id,
        "compose_attempts": 0,
        "started_at": time.monotonic(),
    }


async def record_outcome(
    state: TicketPipelineState,
    deps: Deps,
    outcome: PipelineOutcome,
    *,
    ticket_id: Any = None,
) -> None:
    """Write the evaluation row. Called from every terminal node, without exception.

    Time-to-ticket is only meaningful on the paths that produced a ticket, so it
    is left null elsewhere rather than recorded as zero.
    """
    started = state.get("started_at")
    produced_ticket = outcome in (PipelineOutcome.TICKET_CREATED, PipelineOutcome.TICKET_UPDATED)
    elapsed = time.monotonic() - started if started is not None and produced_ticket else None

    await deps.repo.save_evaluation(
        feature=state["diagnosis"].feature,
        outcome=outcome,
        diagnosis_id=state.get("diagnosis_id"),
        ticket_id=ticket_id,
        compose_attempts=state.get("compose_attempts", 0),
        time_to_ticket_seconds=elapsed,
    )
