"""State carried through the graphs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID

from pydantic import BaseModel, Field

from triage.config import RepoKind
from triage.errors.gate import GateDecision
from triage.mapping.report import MappingReport
from triage.schemas.alert import Alert
from triage.schemas.analysis import AnalysisFindings, AnalysisResult
from triage.schemas.collection import AlertClassification, Collection, Qualification
from triage.schemas.common import Feature, Filled, TimeWindow
from triage.schemas.diagnosis import Diagnosis
from triage.schemas.errors import (
    CodeExceptionContext,
    ErrorCollection,
    ErrorGroup,
    ErrorIssue,
    ErrorTrack,
    SkippedIssue,
)
from triage.schemas.hypothesis import Hypothesis
from triage.schemas.signal import Signal
from triage.schemas.system_map import (
    CommitSource,
    Derivation,
    MappingSource,
    RepoSummary,
    SeedEntry,
    SystemMap,
    TerraformSummary,
)
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

    # The Slack thread the calling feature opened, if any: every notice about one
    # incident belongs under the message that announced it.
    thread_ts: str | None

    # What F2 is reporting about, when the caller is F2 (M8 4.4). A compiled
    # sub-graph is invoked with the parent state filtered to its own schema, so a
    # group and its collection reach the terminal node only by being declared
    # here — and they are declared as one typed carrier rather than as three keys
    # the pipeline would have to know the meaning of.
    exception: CodeExceptionContext | None

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


class CarriedForward(BaseModel):
    """A repository this run deliberately did not re-summarise, and why (ADR-0015).

    Its map rows are unchanged but are now known to be current as of ``commit``.
    """

    repo_url: str
    commit: str
    reason: str


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
    carried_forward: list[CarriedForward]
    summaries: list[Summarised]
    system_map: SystemMap
    failures: list[SummaryFailure]
    unowned: list[str]
    entries_written: int


class MappingState(TypedDict, total=False):
    """One pass of the service mapping (M6).

    Input is ``services``; an empty list is a full pass over every service that
    has alerted inside ``lookback_days``. ``at`` is when the incident that
    prompted the pass fired, so a default-branch fallback reads the branch as it
    stood then rather than as it stands now. Everything else is what the pass
    found, reported back rather than only logged, because an unmapped production
    workload is Triage's own gap.
    """

    services: list[str]
    lookback_days: int
    at: datetime

    seed: list[SeedEntry]
    targets: list[str]
    unclaimed: list[str]
    derivations: list[Derivation]
    entries_written: int
    report: MappingReport


class Deferred(BaseModel):
    """A hypothesis that was ranked but not analysed, and why (ADR-0005).

    It is not discarded: a developer who is told what was *not* looked at can
    reopen it, and a developer who is told nothing repeats the ranking by hand.
    """

    hypothesis: Hypothesis
    reason: Filled


class Investigated(BaseModel):
    """One hypothesis after the branch it was routed to has run.

    ``result`` is ``None`` only for a dependency cause, which no runner examines;
    a hypothesis whose repository or commit could not be resolved carries a
    *failed* result naming that, so the failure paths stay one path.
    """

    hypothesis: Hypothesis
    repo_url: str | None = None
    commit: str | None = None
    base_commit: str | None = None
    commit_source: CommitSource | None = None
    mapping_source: MappingSource | None = None
    paths: list[str] = Field(default_factory=list)
    result: AnalysisResult | None = None

    @property
    def failed(self) -> bool:
        return self.result is not None and not self.result.succeeded

    @property
    def findings(self) -> AnalysisFindings | None:
        if self.result is None or not self.result.succeeded:
            return None
        payload = self.result.result
        return payload if isinstance(payload, AnalysisFindings) else None


class AnalysisState(TypedDict, total=False):
    """Input is ``hypotheses`` plus who they are about; output is ``diagnosis``.

    ``context`` is whatever the calling feature already collected — the F1
    telemetry, the F3 query statistics — passed through to the synthesis prompt
    untouched, because the sub-graph is shared and must not know which of them
    produced it.
    """

    hypotheses: list[Hypothesis]
    feature: Feature
    service: str
    team: str
    signal_id: UUID | None
    context: dict[str, Any]

    selected: list[Hypothesis]
    deferred: list[Deferred]
    investigated: list[Investigated]
    diagnosis: Diagnosis
    synthesis_attempts: int


class IncidentState(AnalysisState, TicketPipelineState, total=False):
    """F1, from a persisted alert to a ticket and a post-mortem (architecture §2.3).

    The input is a ``Signal`` the poller already stored, never a raw webhook body:
    by the time this graph runs, the alert has been in ``error`` for the
    persistence gate (ADR-0018) and somebody owns it.
    """

    signal: Signal
    alert: Alert

    classification: AlertClassification
    window: TimeWindow
    collection: Collection
    followup_done: bool

    qualification: Qualification
    postmortem: str


class CodeExceptionState(AnalysisState, TicketPipelineState, total=False):
    """F2, from one gated error group to a report (M8, ADR-0025, ADR-0026).

    The input is an ``ErrorGroup`` the tick already persisted and the gate already
    took up. It inherits both sub-graph states for the same reason
    :class:`IncidentState` does — the compiled ``analysis`` and ``ticket_pipeline``
    graphs are added as nodes and read their own keys.
    """

    group: ErrorGroup
    window: TimeWindow
    collection: ErrorCollection
    qualification: Qualification


class PollerState(TypedDict, total=False):
    """One tick of the alert poller (ADR-0017).

    Everything it did is reported back rather than only logged: a tick that
    skipped a span, refused an alert as out of scope or launched three runs is
    the only observable the cron has.
    """

    now: datetime | None

    events_seen: int
    created: list[UUID]
    launched: list[UUID]
    recovered: list[Signal]
    out_of_scope: list[UUID]
    unmapped: list[UUID]
    flapping: list[str]
    skipped_span: str | None


class ErrorPollerState(TypedDict, total=False):
    """One tick of the hourly code-exception pass (ADR-0025).

    Everything the tick decided is reported back rather than only logged. Most
    ticks decide nothing — over the reference hour all fifteen occurring issues
    were unchanged — so ``unchanged`` and ``issues_seen`` are the difference
    between a pass that ran and found nothing and a pass that did not run.
    """

    now: datetime | None
    since: datetime | None
    """An operator naming the window, bypassing the watermark and the catch-up clamp."""

    window: TimeWindow
    query: str
    issues_seen: dict[ErrorTrack, int]

    new: list[ErrorIssue]
    regressed: list[ErrorIssue]
    occurring: list[ErrorIssue]
    """Neither new nor regressed, and kept anyway: the only material the
    cumulative escalation has, since Datadog calls an issue new once (ADR-0030)."""
    unchanged: int
    skipped: list[SkippedIssue]
    failures: list[str]
    skipped_span: str | None

    # What the tick made of what it found (M8 phase 2). Every one of these is
    # reported rather than only logged: a tick that held four groups back and
    # deferred two looks exactly like a tick that found nothing, until the
    # numbers are said out loud.
    groups: list[ErrorGroup]
    decisions: list[GateDecision]
    analysing: list[ErrorGroup]
    seen_again: int
    """Groups whose total this tick moved without seeing them arrive."""
    held_back: int
    deferred: list[str]
    unmapped: list[str]
