"""The Diagnosis — Triage's internal conclusion about a signal.

Field for field, this mirrors the nine-point developable-ticket specification in
``docs/ticket-spec.md``. That is deliberate: the ticket pipeline's job is to
render a Diagnosis into prose a developer can act on, not to discover anything
new, so any gap here is a gap the composer cannot paper over.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from triage.schemas.common import (
    Confidence,
    Feature,
    Filled,
    MaybeUnknown,
    TimeWindow,
    is_unknown,
)


class EvidenceKind(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    K8S_EVENT = "k8s_event"
    DASHBOARD = "dashboard"
    COMMIT = "commit"
    DB_STAT = "db_stat"
    OTHER = "other"


class Evidence(BaseModel):
    """One checkable pointer. Spec point 4."""

    kind: EvidenceKind
    description: Filled = Field(description="What this shows, with numbers where they exist.")
    url: str | None = Field(
        default=None,
        description="Direct link to the metric, log query, trace or event. Omit if none exists.",
    )


class Symptom(BaseModel):
    """What was observed, with numbers and a window. Spec point 1."""

    description: Filled = Field(
        description="Observed behaviour including the measured numbers, e.g. "
        "'p95 on /orders rose from 120 ms to 1.4 s'."
    )
    window: TimeWindow


class Impact(BaseModel):
    """Who and what is affected. Spec point 2."""

    users: MaybeUnknown = Field(description="Which users, and how many, are affected.")
    services: list[str] = Field(default_factory=list, description="Affected service names.")
    slos: MaybeUnknown = Field(description="Which SLOs are breached or at risk.")


class Location(BaseModel):
    """Where the fix goes. Spec point 5."""

    repo: MaybeUnknown
    commit: MaybeUnknown = Field(description="The commit deployed when the symptom appeared.")
    paths: list[str] = Field(
        default_factory=list, description="Suspected files, functions or symbols."
    )
    terraform_resource: str | None = Field(
        default=None, description="Terraform module or resource, for infrastructure causes."
    )


class AcceptanceCriterion(BaseModel):
    """The verifiable change. Spec point 6.

    Two fields, not one: a criterion the developer cannot check is not a
    criterion, so the statement always travels with how to verify it.
    """

    statement: Filled = Field(
        description="Verifiable outcome, e.g. 'p95 of /orders back under 300 ms'."
    )
    how_to_verify: Filled = Field(
        description="Exactly where the developer looks to confirm it, before closing."
    )


class RuledOut(BaseModel):
    """A hypothesis already eliminated, so nobody redoes the work. Spec point 8."""

    hypothesis: Filled
    why: Filled = Field(description="The observation that eliminated it.")


class OpenQuestion(BaseModel):
    """Something Triage could not settle. Spec point 9."""

    question: Filled
    why_unresolved: Filled = Field(description="What was missing or inaccessible.")


class Diagnosis(BaseModel):
    """Triage's conclusion about one signal, ready for the ticket pipeline."""

    diagnosis_id: UUID = Field(default_factory=uuid4)
    signal_id: UUID | None = None
    feature: Feature
    service: str
    team: str

    symptom: Symptom
    impact: Impact
    probable_cause: MaybeUnknown
    confidence: Confidence
    confidence_rationale: Filled = Field(
        description="Why this confidence level and not the one above or below it."
    )
    evidence: Annotated[list[Evidence], Field(min_length=1)]
    location: Location
    expected_change: AcceptanceCriterion
    out_of_scope: list[str] = Field(
        default_factory=list, description="What the fix must not touch."
    )
    ruled_out: list[RuledOut] = Field(default_factory=list)
    unknowns: list[OpenQuestion] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _confidence_is_earned(self) -> Diagnosis:
        """Confidence must be supported by what the diagnosis actually contains.

        These are the two ways a plausible-looking diagnosis lies: claiming a
        cause it cannot name, and claiming certainty from a single data point.
        """
        if is_unknown(self.probable_cause) and self.confidence is not Confidence.LOW:
            raise ValueError(
                "probable_cause is unknown, so confidence cannot exceed 'low'; "
                f"got {self.confidence!r}"
            )
        if self.confidence is Confidence.HIGH and len(self.evidence) < 2:
            raise ValueError(
                "confidence 'high' requires at least two independent pieces of evidence; "
                f"got {len(self.evidence)}"
            )
        return self


class DiagnosisDraft(BaseModel):
    """What the ``diagnosis`` tier is asked for: a :class:`Diagnosis` minus the facts it
    must not invent, and minus the validator it must be allowed to fail.

    The repository and the commit are resolved from the hypothesis that was
    analysed, never written by the model, so the cause is chosen by *index* into
    what was analysed rather than by free text. And the draft deliberately carries
    no ``_confidence_is_earned``: a synthesis that cannot earn its confidence has
    to be observable by the node in order to be fed back, and a structured-output
    call that raises leaves nothing to feed back with.
    """

    chosen_hypothesis: int | None = Field(
        default=None,
        description="Index into the analysed hypotheses, as numbered in the prompt. "
        "None when none of them is the cause.",
    )
    symptom: Symptom
    impact: Impact
    probable_cause: MaybeUnknown
    confidence: Confidence
    confidence_rationale: Filled = Field(
        description="Why this confidence level and not the one above or below it."
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Telemetry that supports the cause. Analysis findings are attached "
        "automatically and must not be repeated here.",
    )
    paths: list[str] = Field(
        default_factory=list, description="Suspected files, functions or symbols."
    )
    terraform_resource: str | None = None
    expected_change: AcceptanceCriterion
    out_of_scope: list[str] = Field(default_factory=list)
    ruled_out: list[RuledOut] = Field(
        default_factory=list, description="Hypotheses the analysis eliminated, and what did it."
    )
    unknowns: list[OpenQuestion] = Field(default_factory=list)
