"""Deduplication: update an existing ticket rather than creating a second one."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from triage.config import Thresholds
from triage.db.repo import TicketRecord
from triage.graphs.state import TicketPipelineState
from triage.prompts import render
from triage.runtime import deps_from_runnable_config
from triage.schemas.common import render as render_field
from triage.schemas.diagnosis import Diagnosis
from triage.schemas.ticket import DedupDecision, PipelineOutcome

from .persist import record_outcome


def should_realert(occurrence_count: int, thresholds: Thresholds) -> bool:
    """Whether a recurrence is loud enough to interrupt the team again (ADR-0003).

    Fires at ``dedup_recurrence_alert``, then every ``dedup_recurrence_interval``
    occurrences after it — 3rd, 8th, 13th with the defaults. Every match still
    produces a quiet Slack notice; this decides whether that notice escalates.
    """
    if occurrence_count < thresholds.dedup_recurrence_alert:
        return False
    delta = occurrence_count - thresholds.dedup_recurrence_alert
    return delta % thresholds.dedup_recurrence_interval == 0


def _candidate_view(tickets: list[TicketRecord]) -> list[dict[str, str]]:
    return [
        {"ticket_key": t.jira_key, "summary": t.summary, "state": t.state, "service": t.service}
        for t in tickets
    ]


def _diagnosis_view(diagnosis: Diagnosis) -> dict[str, Any]:
    """The fields dedup actually reasons over. Sending the whole diagnosis buries them."""
    return {
        "service": diagnosis.service,
        "symptom": diagnosis.symptom.description,
        "probable_cause": render_field(diagnosis.probable_cause),
        "location": diagnosis.location.model_dump(mode="json"),
    }


async def dedup_check(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Match against open tickets for the same service.

    The shortlist is a cheap SQL filter; only the judgement is a model call. With
    no open tickets there is nothing to compare, so no call is made at all.
    """
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]

    candidates = await deps.repo.open_tickets_for_service(diagnosis.service)
    if not candidates:
        return {
            "dedup": DedupDecision(
                matched=False,
                reasoning="No open tickets exist for this service.",
            )
        }

    decision = await deps.llm.call(
        "triage",
        render(
            "dedup_check",
            new_diagnosis=_diagnosis_view(diagnosis),
            open_tickets=_candidate_view(candidates),
        ),
        DedupDecision,
    )

    known_keys = {ticket.jira_key for ticket in candidates}
    if decision.matched and decision.ticket_key not in known_keys:
        # The model matched a ticket it was not shown. Treat as no match: acting on
        # a hallucinated key would append evidence to an unrelated team's ticket.
        return {
            "dedup": DedupDecision(
                matched=False,
                reasoning=(
                    f"Discarded match on {decision.ticket_key!r}, which is not among the "
                    f"open tickets offered. Original reasoning: {decision.reasoning}"
                ),
            )
        }
    return {"dedup": decision}


async def update_existing_ticket(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Append evidence to the matched ticket and notify, quietly or loudly."""
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]
    decision = state["dedup"]
    key = decision.ticket_key
    assert key is not None, "update_existing_ticket reached without a matched ticket key"

    ticket = await deps.repo.bump_occurrence(key)
    escalate = should_realert(ticket.occurrence_count, deps.config.thresholds)

    await deps.jira.add_comment(key=key, body=_recurrence_comment(state, ticket))

    channel = deps.config.team(diagnosis.team).slack_channel
    if escalate:
        await deps.repo.mark_alerted(key)
        text = (
            f":repeat: *{key}* has now recurred {ticket.occurrence_count} times — "
            f"{diagnosis.symptom.description} on `{diagnosis.service}`. "
            f"Evidence appended: {ticket.jira_url}"
        )
    else:
        text = (
            f"Recurrence #{ticket.occurrence_count} of *{key}* on `{diagnosis.service}`; "
            f"evidence appended, no new ticket. {ticket.jira_url}"
        )
    await deps.slack.post(channel=channel, text=text)

    await record_outcome(state, deps, PipelineOutcome.TICKET_UPDATED, ticket_id=ticket.id)
    return {
        "outcome": PipelineOutcome.TICKET_UPDATED,
        "ticket_key": key,
        "ticket_url": ticket.jira_url,
    }


def _recurrence_comment(state: TicketPipelineState, ticket: TicketRecord) -> str:
    diagnosis = state["diagnosis"]
    evidence = "\n".join(
        f"- {item.kind.value}: {item.description}" + (f" — {item.url}" if item.url else "")
        for item in diagnosis.evidence
    )
    return (
        f"Recurrence #{ticket.occurrence_count}, seen again at "
        f"{diagnosis.symptom.window.end.isoformat()}.\n\n"
        f"Observed: {diagnosis.symptom.description}\n\n"
        f"New evidence:\n{evidence}\n\n"
        f"Matched by Triage: {state['dedup'].reasoning}"
    )
