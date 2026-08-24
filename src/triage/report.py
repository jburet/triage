"""The incident report — what the release actually delivers (ADR-0023).

Both live F1 runs on 2026-08-24 computed a minute-by-minute qualification, ranked
causes, the hypotheses the analysis floor ruled out and six named unknowns, and
posted four lines saying "No ticket raised — confidence low". The product was
computed and thrown away; only the delivery was missing. This module is that
delivery.

It is a rule, not a model call: everything here is already written down in the
``Diagnosis``, the derived workload and the collection, and re-narrating it
through a tier would cost money to introduce a chance of it saying something
none of the three do.
"""

from __future__ import annotations

from dataclasses import dataclass

from triage.schemas.common import Confidence
from triage.schemas.common import render as render_field
from triage.schemas.diagnosis import Diagnosis

CONFIDENCE_LABEL: dict[Confidence, str] = {
    Confidence.LOW: "low",
    Confidence.MEDIUM: "medium",
    Confidence.HIGH: "high",
}


@dataclass(frozen=True)
class ReportSection:
    """One heading and its body. The unit a split may fall between, never inside."""

    heading: str
    body: str

    def render(self) -> str:
        return f"*{self.heading}*\n{self.body}"


@dataclass(frozen=True)
class SlackReport:
    """One incident, ready to post."""

    service: str
    headline: str
    sections: tuple[ReportSection, ...]
    leads_with_cause: bool

    @property
    def messages(self) -> list[str]:
        return ["\n\n".join([self.headline, *(s.render() for s in self.sections)])]


def _cause(diagnosis: Diagnosis) -> str:
    return render_field(diagnosis.probable_cause)


def render_incident(
    diagnosis: Diagnosis,
    *,
    threshold: Confidence,
) -> SlackReport:
    """One renderer, two framings — the threshold frames rather than routes.

    Above the bar the report leads with the probable cause, because that is what
    the reader acts on. Below it the cause is the one thing the report may not
    lead with, so it leads with what is established.
    """
    confident = diagnosis.confidence.at_least(threshold)
    headline = (
        f":dart: *{diagnosis.service}* — {_cause(diagnosis)}"
        if confident
        else f":mag: *{diagnosis.service}* — {diagnosis.symptom.description}"
    )
    sections = (
        ReportSection("Symptom", f"{diagnosis.symptom.description}\n_{diagnosis.symptom.window}_"),
        ReportSection(
            "Probable cause",
            f"{_cause(diagnosis)}\nConfidence *{CONFIDENCE_LABEL[diagnosis.confidence]}* — "
            f"{diagnosis.confidence_rationale}",
        ),
    )
    return SlackReport(
        service=diagnosis.service,
        headline=headline,
        sections=sections,
        leads_with_cause=confident,
    )
