"""The composed ticket, and the self-review verdict on it.

One field per point of the developable-ticket specification. Keeping the nine
sections as separate fields rather than one rendered blob is what lets
``self_review`` name the section that is missing, and what lets the golden tests
assert completeness without parsing markdown.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from triage.schemas.common import Filled


class TicketSection(StrEnum):
    """The nine required sections. The order is the order they appear in Jira."""

    SYMPTOM = "symptom"
    IMPACT = "impact"
    PROBABLE_CAUSE = "probable_cause"
    EVIDENCE = "evidence"
    LOCATION = "location"
    EXPECTED_CHANGE = "expected_change"
    OUT_OF_SCOPE = "out_of_scope"
    RULED_OUT = "ruled_out"
    UNKNOWNS = "unknowns"

    @property
    def heading(self) -> str:
        return _HEADINGS[self]


_HEADINGS: dict[TicketSection, str] = {
    TicketSection.SYMPTOM: "Symptom",
    TicketSection.IMPACT: "Impact",
    TicketSection.PROBABLE_CAUSE: "Probable cause",
    TicketSection.EVIDENCE: "Evidence",
    TicketSection.LOCATION: "Location",
    TicketSection.EXPECTED_CHANGE: "Expected change",
    TicketSection.OUT_OF_SCOPE: "Out of scope",
    TicketSection.RULED_OUT: "Hypotheses ruled out",
    TicketSection.UNKNOWNS: "Unknowns",
}


class TicketDraft(BaseModel):
    """A Jira issue body, one field per specification section.

    Every section is :data:`~triage.schemas.common.Filled`: a section that has
    nothing to report must say so and say why (\"No hypotheses were eliminated:
    only one cause was consistent with the traces\"), never be left blank.
    """

    summary: Filled = Field(
        max_length=255,
        description="Jira summary line: the symptom and the service, under 120 characters.",
    )
    symptom: Filled
    impact: Filled
    probable_cause: Filled
    evidence: Filled
    location: Filled
    expected_change: Filled
    out_of_scope: Filled
    ruled_out: Filled
    unknowns: Filled

    def section(self, name: TicketSection) -> str:
        return str(getattr(self, name.value))

    def to_markdown(self) -> str:
        body = "\n\n".join(
            f"## {section.heading}\n\n{self.section(section)}" for section in TicketSection
        )
        return f"{body}\n"


class ReviewVerdict(BaseModel):
    """Output of ``self_review``.

    Not a bare boolean: the retry prompt needs to know which sections failed and
    why, otherwise the second attempt is just the first attempt again.
    """

    passes: bool = Field(
        description="True only if a developer could start work without asking a question."
    )
    missing: list[TicketSection] = Field(
        default_factory=list, description="Sections that are absent, vague or unverifiable."
    )
    feedback: str = Field(
        default="", description="What specifically to fix. Empty only when passes is True."
    )


class DedupDecision(BaseModel):
    """Output of ``dedup_check``."""

    matched: bool
    ticket_key: str | None = Field(
        default=None, description="The existing Jira key, when matched is True."
    )
    reasoning: str = Field(description="Why this is, or is not, the same underlying problem.")


class PipelineOutcome(StrEnum):
    """How a run of the ticket pipeline ended. Recorded for self-evaluation."""

    REPORT_POSTED = "report_posted"
    """The release's terminal outcome: one report in the team's channel (ADR-0023)."""
    TICKET_CREATED = "ticket_created"
    TICKET_UPDATED = "ticket_updated"
    BELOW_THRESHOLD = "below_threshold"
    REVIEW_EXHAUSTED = "review_exhausted"


TerminalOutcome = Literal[
    PipelineOutcome.REPORT_POSTED,
    PipelineOutcome.TICKET_CREATED,
    PipelineOutcome.TICKET_UPDATED,
    PipelineOutcome.BELOW_THRESHOLD,
    PipelineOutcome.REVIEW_EXHAUSTED,
]
