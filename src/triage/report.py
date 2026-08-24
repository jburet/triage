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

from collections.abc import Sequence
from dataclasses import dataclass

from triage.schemas.common import Confidence, is_unknown
from triage.schemas.common import render as render_field
from triage.schemas.diagnosis import Diagnosis, Location
from triage.schemas.system_map import CommitSource, MappingSource, WorkloadEntry
from triage.schemas.ticket import TicketSection

CONFIDENCE_LABEL: dict[Confidence, str] = {
    Confidence.LOW: "low",
    Confidence.MEDIUM: "medium",
    Confidence.HIGH: "high",
}


MAPPING_RUNG: dict[MappingSource, str] = {
    MappingSource.IMAGE: "derived from the image this service is running",
    MappingSource.SEED: "named by the architecture document's repository map",
    MappingSource.MAP: "from F0's map, keyed on the name the repository says it deploys as",
    MappingSource.PATTERN: "matched by a `serves` name pattern in config.yaml — nothing "
    "running was observed to say so",
    MappingSource.MANUAL: "declared by hand",
}
"""How this service came to be attributed to this repository (ADR-0019).

Rendered because the rungs are different facts. A repository the running image
named and one a hand-maintained glob picked out are told apart nowhere else in
the report, and a reader who cannot tell them apart reads the second as the
first."""


COMMIT_RUNG: dict[CommitSource, str] = {
    CommitSource.IMAGE_TAG: "the image tag is the commit",
    CommitSource.GITHUB_TAG: "the image tag is a build number, and GitHub's tag of that "
    "name points at this commit",
    CommitSource.DEFAULT_BRANCH: "the repository's default branch as it stood when the "
    "incident fired — not an identified build",
}
"""And how that repository came to be read at this commit (ADR-0020)."""

NO_COMMIT_OBSERVED = "nothing observed which build this service is running"
"""What a pattern mapping has instead of a rung: it saw no image, so it saw no build."""


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


def _open_questions(diagnosis: Diagnosis) -> str:
    count = len(diagnosis.unknowns)
    if count == 0:
        return "Nothing was left open."
    return f"{count} question{'' if count == 1 else 's'} below are still open."


def _bullets(items: Sequence[str], *, empty: str) -> str:
    """A list, or a sentence saying the list is empty and what that means.

    An absent section reads as an oversight and a blank one reads as nothing at
    all. Both are how a reader learns to stop trusting the sections that *are*
    filled, which is why the specification has no representation for empty.
    """
    return "\n".join(f"• {item}" for item in items) if items else f"_{empty}_"


def _symptom(diagnosis: Diagnosis) -> str:
    return f"{diagnosis.symptom.description}\n_Window: {diagnosis.symptom.window}_"


def _impact(diagnosis: Diagnosis) -> str:
    services = ", ".join(f"`{name}`" for name in diagnosis.impact.services)
    return (
        f"*Users:* {render_field(diagnosis.impact.users)}\n"
        f"*SLOs:* {render_field(diagnosis.impact.slos)}\n"
        f"*Services:* {services or '_none named_'}"
    )


def _probable_cause(diagnosis: Diagnosis) -> str:
    return (
        f"{_cause(diagnosis)}\n"
        f"_Confidence *{CONFIDENCE_LABEL[diagnosis.confidence]}* — "
        f"{diagnosis.confidence_rationale}_"
    )


def _evidence(diagnosis: Diagnosis) -> str:
    return _bullets(
        [
            f"[{item.kind.value}] {item.description}" + (f" — {item.url}" if item.url else "")
            for item in diagnosis.evidence
        ],
        empty="No checkable evidence was produced.",
    )


def _expected_change(diagnosis: Diagnosis) -> str:
    return (
        f"{diagnosis.expected_change.statement}\n"
        f"_Verify at: {diagnosis.expected_change.how_to_verify}_"
    )


def _repository_line(location: Location, workload: WorkloadEntry | None) -> str:
    stated = render_field(location.repo)
    if workload is None:
        return f"*Repository:* {stated}"
    rung = MAPPING_RUNG[workload.source]
    if is_unknown(location.repo) and workload.repo_url:
        return f"*Repository:* {stated}\n_The service maps to `{workload.repo_url}`, {rung}._"
    return f"*Repository:* {stated} — _{rung}_"


def _commit_line(location: Location, workload: WorkloadEntry | None) -> str:
    stated = render_field(location.commit)
    if workload is None:
        return f"*Commit:* {stated}"
    rung = COMMIT_RUNG.get(workload.commit_source) if workload.commit_source else None
    return f"*Commit:* {stated} — _{rung or NO_COMMIT_OBSERVED}_"


def _infrastructure_line(workload: WorkloadEntry | None) -> str | None:
    if workload is None or workload.iac_repo is None:
        return None
    paths = ", ".join(f"`{path}`" for path in workload.iac_paths)
    return f"*Infrastructure:* `{workload.iac_repo}` — " + (
        paths or "_no path in it is declared or found for this workload_"
    )


def _location(diagnosis: Diagnosis, workload: WorkloadEntry | None) -> str:
    """Repository, commit, chart and files — each with what said so.

    The diagnosis states where the analysis read; the workload states how this
    service came to be attributed there at all. Both, because they can disagree:
    a run that analysed nothing has no location and the mapping still does.
    """
    location = diagnosis.location
    lines = [_repository_line(location, workload), _commit_line(location, workload)]
    infrastructure = _infrastructure_line(workload)
    if infrastructure:
        lines.append(infrastructure)
    if location.terraform_resource:
        lines.append(f"*Terraform:* `{location.terraform_resource}`")
    lines.append(
        "*Files:* " + (", ".join(f"`{path}`" for path in location.paths) or "_none suspected_")
    )
    return "\n".join(lines)


def _headline(diagnosis: Diagnosis, *, threshold: Confidence, confident: bool) -> str:
    """Two lines: what the reader should take away, then how far to trust it."""
    level = CONFIDENCE_LABEL[diagnosis.confidence]
    bar = CONFIDENCE_LABEL[threshold]
    if confident:
        return (
            f":dart: *{diagnosis.service}* — {_cause(diagnosis)}\n"
            f"Confidence *{level}*, at or above the *{bar}* "
            f"{diagnosis.feature.value} needs to lead with a cause."
        )
    return (
        f":mag: *{diagnosis.service}* — {diagnosis.symptom.description}\n"
        f"Confidence *{level}*, below the *{bar}* {diagnosis.feature.value} needs to "
        f"lead with a cause, so this is what is established. {_open_questions(diagnosis)}"
    )


def render_incident(
    diagnosis: Diagnosis,
    workload: WorkloadEntry | None = None,
    *,
    threshold: Confidence,
) -> SlackReport:
    """One renderer, two framings — the threshold frames rather than routes.

    Above the bar the report leads with the probable cause, because that is what
    the reader acts on. Below it the cause is the one thing the report may not
    lead with, so it leads with what is established.
    """
    confident = diagnosis.confidence.at_least(threshold)
    headline = _headline(diagnosis, threshold=threshold, confident=confident)
    bodies: dict[TicketSection, str] = {
        TicketSection.SYMPTOM: _symptom(diagnosis),
        TicketSection.IMPACT: _impact(diagnosis),
        TicketSection.PROBABLE_CAUSE: _probable_cause(diagnosis),
        TicketSection.EVIDENCE: _evidence(diagnosis),
        TicketSection.LOCATION: _location(diagnosis, workload),
        TicketSection.EXPECTED_CHANGE: _expected_change(diagnosis),
        TicketSection.OUT_OF_SCOPE: _bullets(
            diagnosis.out_of_scope,
            empty="The diagnosis named nothing the fix must avoid.",
        ),
        TicketSection.RULED_OUT: _bullets(
            [f"{item.hypothesis} — {item.why}" for item in diagnosis.ruled_out],
            empty="The diagnosis eliminated no hypothesis, so nothing here has been "
            "checked and dismissed for you.",
        ),
        TicketSection.UNKNOWNS: _bullets(
            [f"{item.question} — {item.why_unresolved}" for item in diagnosis.unknowns],
            empty="The diagnosis left no question open.",
        ),
    }
    sections = tuple(ReportSection(section.heading, bodies[section]) for section in TicketSection)
    return SlackReport(
        service=diagnosis.service,
        headline=headline,
        sections=sections,
        leads_with_cause=confident,
    )
