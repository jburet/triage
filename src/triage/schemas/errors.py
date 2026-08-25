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
