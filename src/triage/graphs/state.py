"""State carried through the ticket pipeline."""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from triage.schemas.diagnosis import Diagnosis
from triage.schemas.ticket import DedupDecision, PipelineOutcome, ReviewVerdict, TicketDraft


class TicketPipelineState(TypedDict, total=False):
    """Input is ``diagnosis``; everything else is filled as the graph runs."""

    diagnosis: Diagnosis
    diagnosis_id: UUID | None

    dedup: DedupDecision
    draft: TicketDraft
    verdict: ReviewVerdict
    compose_attempts: int

    outcome: PipelineOutcome
    ticket_key: str | None
    ticket_url: str | None

    # Monotonic clock reading taken at entry, for the time-to-ticket metric.
    started_at: float
