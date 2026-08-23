"""State carried through the graphs."""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel, Field

from triage.config import RepoKind
from triage.schemas.diagnosis import Diagnosis
from triage.schemas.system_map import RepoSummary, SystemMap, TerraformSummary
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


class RepoRef(BaseModel):
    """A repository to summarise, at a ref.

    ``commit`` is optional because the two callers know different things: a merge
    webhook names the commit it just merged, while a scheduled full pass only
    knows it wants the default branch. Left unset, the clone resolves the tip and
    the map records no commit rather than recording the word ``HEAD``, which
    ADR-0006's diff could not use.
    """

    url: str
    commit: str | None = None


class MergeEvent(BaseModel):
    """A merge to ``main``: the trigger for the incremental refresh (ADR-0006)."""

    repo_url: str
    commit: str


class RepoTarget(BaseModel):
    """One repository this run will summarise, joined with what config.yaml knows."""

    url: str
    kind: RepoKind
    team: str | None = Field(default=None, description="None when no team is declared for it.")
    commit: str | None = None


class Summarised(BaseModel):
    """One repository's summary, still paired with the target it came from.

    The target carries the owner and the commit, which the summary itself has no
    way to know — the merge into map entries needs both halves.
    """

    target: RepoTarget
    summary: RepoSummary | TerraformSummary


class SummaryFailure(BaseModel):
    """A repository this run could not summarise, and why. Never fatal to the run."""

    repo_url: str
    reason: str


class CartographyState(TypedDict, total=False):
    """Input is a ``repos`` list or a ``merge_event``; everything else is filled as it runs.

    ``full`` is the weekly cron's entry point (ADR-0006): re-summarise regardless
    of what changed.
    """

    repos: list[RepoRef]
    merge_event: MergeEvent | None
    full: bool

    targets: list[RepoTarget]
    summaries: list[Summarised]
    system_map: SystemMap
    failures: list[SummaryFailure]
    unowned: list[str]
    entries_written: int
