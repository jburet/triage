"""What one mapping pass could and could not attribute (M6 4.3).

An unmapped production workload is not a team's problem, it is Triage's own gap:
nothing it says about that service will be worth reading until the gap closes.
So the pass reports itself, and reports the mapped services too — because a
mapping derived from the running image and one guessed from a name pattern are
different facts, and only a count that separates them says how much of the map
is actually observed.

Pure functions over the derivations, so the same report reaches Slack and the
terminal without either of them owning it.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from triage.schemas.system_map import Derivation, MappingOutcome, MappingSource

ON_RECORD = (MappingOutcome.MAPPED, MappingOutcome.UNCHANGED)
"""Both leave a row behind: ``unchanged`` is a mapping this pass declined to rewrite."""


class ReportLine(BaseModel):
    """One service, and the one thing about it worth reading in a channel."""

    service: str
    detail: str


class MappingReport(BaseModel):
    """One pass, counted by what answered for each service.

    The four categories the plan names, plus one the run turned up: a workload
    whose repository resolved but whose chart did not. It is mapped, so it is
    counted as mapped; but an ``iac_analysis`` against it is back to selecting by
    glob, which is what answered Unknown three times on 2026-08-23, and the only
    trace of it today is a log line nobody reads.
    """

    services: int = 0
    by_image: list[ReportLine] = Field(default_factory=list)
    by_pattern: list[ReportLine] = Field(default_factory=list)
    unmapped: list[ReportLine] = Field(default_factory=list)
    conflicting: list[ReportLine] = Field(default_factory=list)
    without_chart: list[ReportLine] = Field(default_factory=list)
    unclaimed: list[str] = Field(default_factory=list)


def _mapped_detail(derivation: Derivation) -> str:
    entry = derivation.entry
    assert entry is not None
    where = entry.image_digest or entry.image or "no image observed"
    commit = entry.commit_source.value if entry.commit_source else "no commit resolved"
    return f"{entry.repository} at {where}, {commit}"


def summarise(derivations: Sequence[Derivation], unclaimed: Sequence[str] = ()) -> MappingReport:
    """Sort one pass's derivations into the categories a reader acts on."""
    report = MappingReport(services=len(derivations), unclaimed=list(unclaimed))
    for derivation in derivations:
        line = ReportLine(service=derivation.service, detail=derivation.reason)
        entry = derivation.entry
        if derivation.outcome is MappingOutcome.CONFLICT:
            report.conflicting.append(line)
        elif entry is None or derivation.outcome not in ON_RECORD:
            report.unmapped.append(line)
        else:
            mapped = ReportLine(service=derivation.service, detail=_mapped_detail(derivation))
            side = report.by_image if entry.source is MappingSource.IMAGE else report.by_pattern
            side.append(mapped)
            if entry.iac_repo_url is not None and not entry.iac_paths:
                report.without_chart.append(
                    ReportLine(
                        service=derivation.service,
                        detail=f"{entry.iac_repo} was resolved, the chart inside it was not",
                    )
                )
    return report


def _block(title: str, lines: Sequence[ReportLine]) -> list[str]:
    if not lines:
        return []
    return [f"*{title}* ({len(lines)}):", *(f"• `{one.service}` — {one.detail}" for one in lines)]


def render(report: MappingReport) -> str:
    """The whole report as one message, for Slack and for the terminal alike."""
    counted = "1 service" if report.services == 1 else f"{report.services} services"
    lines = [f":jigsaw: Service mapping: {counted}."]
    lines += _block("Mapped from the running image", report.by_image)
    lines += _block("Mapped from a name pattern only", report.by_pattern)
    lines += _block("Not mapped", report.unmapped)
    lines += _block("Conflicting", report.conflicting)
    lines += _block("Mapped, chart not found", report.without_chart)
    if report.unclaimed:
        lines.append(
            "*In the seed, declared by no team in config.yaml:* "
            + ", ".join(f"`{name}`" for name in report.unclaimed)
        )
    return "\n".join(lines)
