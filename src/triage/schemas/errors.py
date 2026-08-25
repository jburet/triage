"""What Datadog Error Tracking says about one code exception (F2, ADR-0025).

Observed facts, not model output, so nothing here is a :class:`Filled` or an
:class:`Unknown` — those exist to stop a model asserting what it did not know,
and an API response either carried a field or did not.

The shape is the *measured* one. Against the org on 2026-08-25 an issue always
named its exception type, the file and the function it was raised in — 202 of
202 over a week — and almost never named a version: ``first_seen_version`` came
back as the empty string on 15 of 15 issues in the reference hour and on roughly
nine in ten over a month. An empty version string is therefore parsed as
``None``, because a field that is present and blank is an absence, and the
report that says "first seen on version X" must not be able to say "first seen
on version ''".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ErrorTrack(StrEnum):
    """Which telemetry Error Tracking grouped the issue from.

    ``rum`` is deliberately absent: browser errors have no source maps and no
    cartography for the front-end repositories, so they would resolve to no
    repository and become noise (M8, out of scope).
    """

    TRACE = "trace"
    LOGS = "logs"


class ErrorPersona(StrEnum):
    """Whose errors a search asks for. F2 is a backend feature."""

    ALL = "ALL"
    BACKEND = "BACKEND"
    FRONTEND = "FRONTEND"


class Novelty(StrEnum):
    """Why a tick is looking at an issue at all.

    Kept apart because they are different reports: a defect nobody has seen and
    a fix that did not hold are not the same news. It lives with the schema
    rather than with the rule that decides it, because the group carries it too
    and a schema may not import the rules that fill it.
    """

    NEW = "new"
    REGRESSED = "regressed"


class ErrorIssue(BaseModel):
    """One Error Tracking issue in one service, with its count over a window.

    ``occurrences`` is the search's own ``total_count`` for the window asked
    about, joined here from the ``data`` half of the envelope onto the
    attributes in the ``included`` half. The two only arrive together because
    the search is sent ``include=issue``; without it the answer is a list of
    ids and counts and there is nothing to decide anything on.
    """

    issue_id: str
    track: ErrorTrack
    service: str
    occurrences: int = Field(default=0, ge=0)

    error_type: str | None = None
    error_message: str | None = None
    file_path: str | None = None
    function_name: str | None = None

    first_seen: datetime
    last_seen: datetime
    first_seen_version: str | None = None
    last_seen_version: str | None = None

    regressed_at: datetime | None = None
    resolved_at: datetime | None = None

    state: str
    platform: str | None = None
    languages: list[str] = Field(default_factory=list)
    is_crash: bool = False

    @property
    def source_location(self) -> str | None:
        """The file and function to hand an analysis, as one string."""
        if self.file_path is None:
            return None
        return f"{self.file_path}:{self.function_name}" if self.function_name else self.file_path


class SkippedIssue(BaseModel):
    """An issue a tick refused to analyse, and why (behaviour 1.4).

    Recorded rather than dropped: a tick that quietly ignores half of what came
    back is a tick nobody can tell from one that came back empty.
    """

    issue_id: str
    service: str
    reason: str


class ErrorGroupStatus(StrEnum):
    """The life of one error group, as ``SignalStatus`` is for one alert cycle.

    The gate is volume rather than duration (ADR-0025), so the state that
    matters is whether a group has ever been taken up: ``open`` is persisted
    with its count and nothing more, which is the common outcome and the one a
    tick has to report or it looks like a pass that found nothing.
    """

    OPEN = "open"
    ANALYSING = "analysing"
    REPORTED = "reported"
    UNMAPPED = "unmapped"
    """No repository resolves for the service, so there is no tree to read
    (ADR-0026). Reported as Triage's own gap, never analysed."""


class ErrorGroup(BaseModel):
    """One defect, however many tenants raise it (ADR-0026).

    Two halves. The first is derived by :mod:`triage.errors.grouping` from what
    Datadog returned this tick — the key, the location, the services and their
    counts — and is recomputed identically every tick, which is why the fourth
    occurrence finds the first one's row without anything having stored a
    pointer to it. The second half is the lifecycle the repository keeps: the
    cumulative count the escalation reads, how many times the group has been
    taken up, and the Slack thread every message about it goes into.

    ``services`` is deliberately a count per service and not a total. The honest
    cost of grouping across tenants is that a defect only one customer hits
    reads as a platform bug; a group that is 99% one tenant looks different from
    one spread evenly, and that difference only survives if nothing sums it away.

    Nothing here claims more than the search answered. There is no collected
    evidence on a group and no promise there ever will be: measured on
    2026-08-25, a query rebuilt from an issue's own fields returns zero spans and
    zero logs for an issue claiming 6,344 occurrences, because the error spans
    are sampled away and the logs are barely shipped.
    """

    key: str = Field(description="The rule's own output: type, location and repository.")
    error_type: str
    file_path: str
    function_name: str | None = None

    repository: str | None = Field(
        default=None, description="Repository name; None when nothing claims the services."
    )
    repo_url: str | None = None
    team: str | None = None

    track: ErrorTrack
    novelty: Novelty

    services: dict[str, int] = Field(
        default_factory=dict, description="Occurrences this tick, per service. Never summed away."
    )
    occurrences: int = Field(default=0, ge=0, description="This tick, across every service.")
    issue_ids: list[str] = Field(default_factory=list)
    sample_message: str | None = Field(
        default=None,
        description=(
            "One issue's message, as an example and not as the group's identity — "
            "the six tenants of the reference hour carry six different queried "
            "entities in one message shape."
        ),
    )

    first_seen: datetime
    last_seen: datetime
    first_seen_version: str | None = None
    last_seen_version: str | None = None

    unanalysable_reason: str | None = None

    status: ErrorGroupStatus = ErrorGroupStatus.OPEN
    cumulative_occurrences: int = Field(
        default=0,
        ge=0,
        description=(
            "Across every tick that has seen this group. Zero on a group as the "
            "rule derived it: the total belongs to the repository, which is what "
            "lets an upsert tell one tick's observation from a group read back."
        ),
    )
    cumulative_services: dict[str, int] = Field(default_factory=dict)
    analysis_count: int = Field(
        default=0,
        ge=0,
        description="How many times the group has been taken up — which occurrence a report is.",
    )
    analysed_at_cumulative: int = Field(
        default=0,
        ge=0,
        description="Cumulative count when it was last taken up; the escalation counts from here.",
    )
    last_analysed_at: datetime | None = None
    thread_ts: str | None = Field(
        default=None,
        description="The Slack thread every message about this group replies under, across ticks.",
    )
    first_report_url: str | None = Field(
        default=None, description="Permalink of the first report, so a later one can link it."
    )

    @property
    def analysable(self) -> bool:
        """Whether there is a tree to read. A group without one is reported, not analysed."""
        return self.repository is not None

    @property
    def source_location(self) -> str:
        return f"{self.file_path}:{self.function_name}" if self.function_name else self.file_path
