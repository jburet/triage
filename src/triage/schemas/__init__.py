"""Pydantic contracts shared by every graph.

``docs/ticket-spec.md`` is the normative source for these shapes; the modules
here are its executable form.
"""

from triage.schemas.common import (
    Confidence,
    Feature,
    Filled,
    MaybeUnknown,
    TimeWindow,
    Unknown,
    is_unknown,
    render,
)
from triage.schemas.diagnosis import (
    AcceptanceCriterion,
    Diagnosis,
    Evidence,
    EvidenceKind,
    Impact,
    Location,
    OpenQuestion,
    RuledOut,
    Symptom,
)
from triage.schemas.hypothesis import CauseType, Hypothesis
from triage.schemas.signal import Signal, SignalStatus
from triage.schemas.ticket import (
    DedupDecision,
    PipelineOutcome,
    ReviewVerdict,
    TicketDraft,
    TicketSection,
)

__all__ = [
    "AcceptanceCriterion",
    "CauseType",
    "Confidence",
    "DedupDecision",
    "Diagnosis",
    "Evidence",
    "EvidenceKind",
    "Feature",
    "Filled",
    "Hypothesis",
    "Impact",
    "Location",
    "MaybeUnknown",
    "OpenQuestion",
    "PipelineOutcome",
    "ReviewVerdict",
    "RuledOut",
    "Signal",
    "SignalStatus",
    "Symptom",
    "TicketDraft",
    "TicketSection",
    "TimeWindow",
    "Unknown",
    "is_unknown",
    "render",
]
