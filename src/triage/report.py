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

from triage.errors.paths import enclosing_function
from triage.schemas.collection import CollectorStatus
from triage.schemas.common import Confidence, is_unknown
from triage.schemas.common import render as render_field
from triage.schemas.diagnosis import Diagnosis, Location
from triage.schemas.errors import CommitChoice, ErrorCollection, ErrorGroup
from triage.schemas.system_map import CommitSource, MappingSource, WorkloadEntry
from triage.schemas.ticket import TicketSection

MAX_MESSAGE_CHARS = 3900
"""Where the report is cut, and why it is cut here rather than left to Slack.

``chat.postMessage`` accepts forty thousand characters in ``text``, but past
about four thousand Slack breaks the message up itself, at a boundary it
chooses — which lands mid-sentence, mid-evidence-item, mid-link. Cutting first,
between sections, is the only way the break is somewhere a reader can follow.
The remainder under four thousand is the part marker's."""

_MARKER_BUDGET = 64
"""Reserved out of every part for the ``Part i of n`` line, whose length is not
known until the packing that needs it has already happened."""


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

    def chunks(self, limit: int) -> list[str]:
        """This section as one block, or as several that each fit inside ``limit``.

        A section too long for any message is continued rather than truncated,
        and the continuation is broken between whole lines — an evidence item is
        one line, so an item is never cut in half. A single line longer than the
        limit is emitted whole and over it: nothing can be done with it that is
        better than letting Slack render it.
        """
        rendered = self.render()
        if len(rendered) <= limit:
            return [rendered]
        blocks: list[str] = []
        current: list[str] = []
        head = self.heading
        for line in self.body.split("\n"):
            candidate = "\n".join([f"*{head}*", *current, line])
            if current and len(candidate) > limit:
                blocks.append("\n".join([f"*{head}*", *current]))
                head, current = f"{self.heading} (continued)", [line]
            else:
                current.append(line)
        blocks.append("\n".join([f"*{head}*", *current]))
        return blocks


@dataclass(frozen=True)
class SlackReport:
    """One incident, ready to post."""

    service: str
    headline: str
    sections: tuple[ReportSection, ...]
    leads_with_cause: bool

    @property
    def messages(self) -> list[str]:
        """What to post, in order. One message unless the report will not fit.

        A section is never split, even when it alone is over the cap: an evidence
        list cut in half is exactly the failure this exists to avoid, and a
        single oversized section is Slack's problem to render rather than ours
        to mangle.
        """
        limit = MAX_MESSAGE_CHARS - _MARKER_BUDGET
        blocks = [self.headline]
        for section in self.sections:
            blocks.extend(section.chunks(limit))
        parts = _pack(blocks, limit)
        if len(parts) == 1:
            return parts
        return [
            f"_Part {index} of {len(parts)} — `{self.service}`._\n\n{part}"
            for index, part in enumerate(parts, start=1)
        ]


def _pack(blocks: Sequence[str], limit: int) -> list[str]:
    """Group blocks into as few messages as fit, cutting only between them."""
    parts: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if current and len(candidate) > limit:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


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


# -- F2: one recurring code exception (M8 4.4) ---------------------------------

DATADOG_APP = "https://app.datadoghq.eu"
"""The org's Datadog. Its deep-link shape has never been opened from a report."""

EXCEPTION_HEADING = "Exception"
"""The tenth section, and the only one not in ``docs/ticket-spec.md``.

The nine are about a symptom; an error group *is* an identity — a type, a
message, a place in the code, a count and a set of tenants — and every one of
those is a fact Datadog handed over rather than something the run inferred. A
reader who has to reconstruct them from the symptom paragraph is reading a worse
report than the one Triage was given."""


def issue_url(issue_id: str) -> str:
    return f"{DATADOG_APP}/apm/error-tracking/issue/{issue_id}"


NAMED_SERVICES = 10
"""How many tenants a report names before it starts counting them.

One PSQLException grouped 66 tenants on 2026-08-25. Every one of them inline is
a wall nobody reads; ten of them with the other fifty-six dropped is the failure
the per-service counts exist to prevent (ADR-0026). So the tail is summed.
"""


def _seen_in(group: ErrorGroup) -> str:
    ordered = sorted(group.services.items(), key=lambda item: (-item[1], item[0]))
    if not ordered:
        return "_no service_"
    named, tail = ordered[:NAMED_SERVICES], ordered[NAMED_SERVICES:]
    seen = " · ".join(f"`{service}` {count:,}" for service, count in named)
    if not tail:
        return seen
    return (
        f"{seen} · _and {len(tail)} more "
        f"tenant{'' if len(tail) == 1 else 's'}, "
        f"{sum(count for _, count in tail):,} occurrences between them_"
    )


def _versions(group: ErrorGroup) -> str:
    """Both versions, or the absence that is the normal case (measured, M8 phase 1)."""
    first, last = group.first_seen_version, group.last_seen_version
    if not first and not last:
        return (
            "Error Tracking recorded no application version for this exception, which is "
            "the usual case in this org — so nothing here says which release it entered at."
        )
    return f"first seen on `{first or 'unrecorded'}`, last seen on `{last or 'unrecorded'}`"


def _raised_at(group: ErrorGroup, source_caveat: str | None) -> str:
    method = enclosing_function(group.function_name)
    where = f"`{group.file_path}`"
    if group.function_name:
        where += f" in `{group.function_name}`"
        if method and method != group.function_name:
            where += f" — the method `{method}`"
    return where + (f"\n_{source_caveat}_" if source_caveat else "")


def _recurrence(group: ErrorGroup) -> str | None:
    """Which report this is, when it is not the first (behaviour 2.5)."""
    if group.analysis_count <= 1:
        return None
    ordinal = group.analysis_count
    first = (
        f" The first is {group.first_report_url}."
        if group.first_report_url
        else " The first is at the top of this thread."
    )
    return (
        f"*Report {ordinal}* for this group — {group.cumulative_occurrences:,} occurrences "
        f"across every tick that has seen it.{first}"
    )


def _counted_over(group: ErrorGroup) -> str:
    """The clock this tick's count was measured on, or that it has none.

    The end carries its own date whenever the window crosses midnight: a
    backfill over a day rendered "between 2026-08-24 07:33 and 07:33 UTC",
    which reads as no window at all.
    """
    window = group.counted_over
    if window is None:
        return "in this tick"
    end = "%H:%M" if window.start.date() == window.end.date() else "%Y-%m-%d %H:%M"
    return f"between {window.start:%Y-%m-%d %H:%M} and {window.end:{end}} UTC"


def _exception_section(group: ErrorGroup, source_caveat: str | None) -> ReportSection:
    """The exception's own identity (behaviour 4.4).

    The occurrence span is the group's own ``first_seen``/``last_seen``, never
    the collection's window: a tick replaying six hours collects over the last
    one, and reading the count of the first against the clock of the second
    dates a burst to an hour it did not happen in.
    """
    lines = [
        f"*Type:* `{group.error_type}`",
        f"*Message:* {group.sample_message or '_the issue carried none_'}",
        f"*Raised at:* {_raised_at(group, source_caveat)}",
        f"*Occurrences:* {group.occurrences:,} {_counted_over(group)}, across "
        f"{len(group.services)} "
        f"service{'' if len(group.services) == 1 else 's'} of the same repository",
        f"*Seen in:* {_seen_in(group)}",
        f"*Versions:* {_versions(group)}",
        "*Issue:* " + ", ".join(issue_url(issue) for issue in group.issue_ids)
        if group.issue_ids
        else "*Issue:* _no Datadog issue id was recorded_",
    ]
    recurrence = _recurrence(group)
    if recurrence:
        lines.append(recurrence)
    return ReportSection(EXCEPTION_HEADING, "\n".join(lines))


def _telemetry(collection: ErrorCollection) -> list[str]:
    """What each collector found, and — where it found nothing — which nothing.

    ADR-0027: an absence Datadog is discarding is a finding, and it is the one
    finding an F2 report in this org reliably has. It is listed beside the
    evidence rather than instead of it, because a reader has to be able to tell
    "nothing was looked for" from "everything was looked for and discarded".
    """
    return [
        f"[{result.collector.value}] {result.status.value}"
        + (f" — {result.detail}" if result.detail else "")
        for result in collection.results
        if result.status is not CollectorStatus.OK
    ]


def _exception_evidence(diagnosis: Diagnosis, collection: ErrorCollection) -> str:
    body = _evidence(diagnosis)
    gaps = _telemetry(collection)
    if not gaps:
        return body
    return (
        body
        + "\n\n_What was searched for and not found:_\n"
        + "\n".join(f"• {line}" for line in gaps)
    )


def _exception_repository(
    diagnosis: Diagnosis, group: ErrorGroup, workload: WorkloadEntry | None
) -> str:
    """The repository, which F2 always knows even when no analysis selected one.

    The diagnosis states where the analysis *read*, and an F2 run whose analyses
    all failed states nothing. But the grouping rule already resolved these
    tenants to one repository — that is what made them one group (ADR-0026) — and
    a report that says "Unknown" about the one thing it is certain of teaches a
    reader to stop believing the sections that are filled."""
    stated = _repository_line(diagnosis.location, workload)
    if not is_unknown(diagnosis.location.repo) or not group.repo_url:
        return stated
    return (
        f"*Repository:* `{group.repo_url}`\n_The tenants raising this exception all run "
        f"that repository, which is what made them one group; no analysis selected a "
        f"location of its own, so nothing narrower than the repository is established._"
    )


def _exception_location(
    diagnosis: Diagnosis,
    group: ErrorGroup,
    workload: WorkloadEntry | None,
    commit: CommitChoice,
) -> str:
    """As F1's, except the commit line is F2's own choice rather than the map's.

    F1 reads whatever the workload is running because that is what alerted. F2
    asks a different question — what did the code look like when this appeared —
    so the rung has to be the one :mod:`triage.errors.versions` picked, and the
    fallback has to read as one (ADR-0019, ADR-0020).
    """
    location = diagnosis.location
    lines = [
        _exception_repository(diagnosis, group, workload),
        f"*Commit:* {commit.commit or '_none could be resolved_'} — _{commit.rung}_",
    ]
    infrastructure = _infrastructure_line(workload)
    if infrastructure:
        lines.append(infrastructure)
    lines.append(
        "*Files:* " + (", ".join(f"`{path}`" for path in location.paths) or "_none suspected_")
    )
    return "\n".join(lines)


def _exception_headline(
    diagnosis: Diagnosis, group: ErrorGroup, *, threshold: Confidence, confident: bool
) -> str:
    level = CONFIDENCE_LABEL[diagnosis.confidence]
    bar = CONFIDENCE_LABEL[threshold]
    name = group.error_type.rsplit(".", 1)[-1]
    where = group.repository or diagnosis.service
    tenants = len(group.services)
    if confident:
        return (
            f":dart: *{where}* — {_cause(diagnosis)}\n"
            f"`{name}` {group.occurrences:,} times in {tenants} "
            f"tenant{'' if tenants == 1 else 's'}. Confidence *{level}*, at or above the "
            f"*{bar}* F2 needs to lead with a cause."
        )
    return (
        f":bug: *{where}* — `{name}` raised {group.occurrences:,} times in {tenants} "
        f"tenant{'' if tenants == 1 else 's'}, at {group.source_location}\n"
        f"Confidence *{level}*, below the *{bar}* F2 needs to lead with a cause, so this "
        f"is what is established. {_open_questions(diagnosis)}"
    )


def render_code_exception(
    diagnosis: Diagnosis,
    group: ErrorGroup,
    workload: WorkloadEntry | None,
    collection: ErrorCollection,
    *,
    commit: CommitChoice,
    source_caveat: str | None = None,
    threshold: Confidence,
) -> SlackReport:
    """The nine sections of the ticket spec, with the exception's identity in front.

    Sibling of :func:`render_incident` and sharing every section helper with it,
    because a report a developer reads twice a week must not have two layouts.
    Three things differ, and each of them is a fact F1 does not have: the header,
    the commit rung, and the telemetry that was searched for and discarded.
    """
    confident = diagnosis.confidence.at_least(threshold)
    bodies: dict[TicketSection, str] = {
        TicketSection.SYMPTOM: _symptom(diagnosis),
        TicketSection.IMPACT: _impact(diagnosis),
        TicketSection.PROBABLE_CAUSE: _probable_cause(diagnosis),
        TicketSection.EVIDENCE: _exception_evidence(diagnosis, collection),
        TicketSection.LOCATION: _exception_location(diagnosis, group, workload, commit),
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
    sections = (
        _exception_section(group, source_caveat),
        *(ReportSection(section.heading, bodies[section]) for section in TicketSection),
    )
    return SlackReport(
        service=group.repository or diagnosis.service,
        headline=_exception_headline(diagnosis, group, threshold=threshold, confident=confident),
        sections=sections,
        leads_with_cause=confident,
    )
