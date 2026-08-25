"""Parsing the Error Tracking envelope, and the two rules a tick applies to it.

Three things, all pure, all measured against ``tests/fixtures/datadog/errors/
org_20260825_1h/``.

**Parse.** The search answers in two halves — ``data`` carries an id and the
occurrence count over the window, ``included`` carries the attributes — and they
are only joined here. An id with no attributes is dropped rather than carried as
a bare id: nothing downstream can group, gate or analyse an issue it knows
nothing about.

**Is it a code exception?** An exception type and a *file* are what make an
issue something a developer can be pointed at. A function name without a file
is not a location — ``$anonfun$load$6`` opens nothing — so it does not count,
and an issue that fails the rule is skipped with the missing half named rather
than silently dropped.

**Is it new?** Datadog states both halves itself: ``first_seen`` for a defect
nobody has seen, and a ``regression`` block for a fix that did not hold. They
are kept apart because they are different reports. An issue that is neither —
the overwhelming majority, 15 of 15 in the reference hour — produces nothing,
which is the whole reason an hourly pass over a busy org is cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from triage.config import Config
from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorIssue, ErrorTrack


class Novelty(StrEnum):
    """Why this tick is looking at an issue at all."""

    NEW = "new"
    REGRESSED = "regressed"


def _moment(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _text(value: Any) -> str | None:
    """A present-but-blank attribute is an absence, not a value."""
    return value.strip() or None if isinstance(value, str) else None


def parse_issues(payload: dict[str, Any], track: ErrorTrack) -> list[ErrorIssue]:
    """The issues in one search answer, counts and attributes joined."""
    counts = {
        entry.get("id"): (entry.get("attributes") or {}).get("total_count") or 0
        for entry in payload.get("data") or []
    }
    issues = []
    for entry in payload.get("included") or []:
        issue = _issue(entry, track, counts)
        if issue is not None:
            issues.append(issue)
    return issues


def _issue(entry: dict[str, Any], track: ErrorTrack, counts: dict[Any, int]) -> ErrorIssue | None:
    attributes = entry.get("attributes") or {}
    first_seen = _moment(attributes.get("first_seen"))
    last_seen = _moment(attributes.get("last_seen"))
    issue_id = entry.get("id")
    if not isinstance(issue_id, str) or first_seen is None or last_seen is None:
        return None
    regression = attributes.get("regression") or {}
    return ErrorIssue(
        issue_id=issue_id,
        track=track,
        service=str(attributes.get("service") or ""),
        occurrences=counts.get(issue_id, 0),
        error_type=_text(attributes.get("error_type")),
        error_message=_text(attributes.get("error_message")),
        file_path=_text(attributes.get("file_path")),
        function_name=_text(attributes.get("function_name")),
        first_seen=first_seen,
        last_seen=last_seen,
        first_seen_version=_text(attributes.get("first_seen_version")),
        last_seen_version=_text(attributes.get("last_seen_version")),
        regressed_at=_moment(regression.get("regressed_at")),
        resolved_at=_moment(regression.get("resolved_at")),
        state=str(attributes.get("state") or "UNKNOWN"),
        platform=_text(attributes.get("platform")),
        languages=[str(language) for language in attributes.get("languages") or []],
        is_crash=bool(attributes.get("is_crash")),
    )


def not_a_code_exception(issue: ErrorIssue) -> str | None:
    """Why this is not something a developer can be pointed at, or None."""
    missing = []
    if issue.error_type is None:
        missing.append("names no exception type")
    if issue.file_path is None:
        missing.append("names no source location")
    if not missing:
        return None
    return f"{issue.service} {' and '.join(missing)}, so there is nothing to analyse"


def novelty(issue: ErrorIssue, window: TimeWindow) -> Novelty | None:
    """New in this window, regressed in this window, or neither."""
    if window.start <= issue.first_seen <= window.end:
        return Novelty.NEW
    if issue.regressed_at is not None and window.start <= issue.regressed_at <= window.end:
        return Novelty.REGRESSED
    return None


def environment_filter(config: Config) -> str | None:
    """The ``env:`` filter a tick sends, or None when no team watches anything.

    The documented departure from ADR-0017 (ADR-0025). That rule — never read an
    environment from an ``env:`` tag — was measured on Kubernetes monitor alerts,
    which carry no usable one. APM events do: ``env:prod`` returned all fifteen
    issues of the reference hour and ``env:preprod`` returned none. So the
    environments Triage watches are a filter Datadog applies inside the search,
    and an issue from an environment no team configured is never returned rather
    than returned and dropped.

    Nothing watched is not everything watched: it returns None, and the tick
    makes no call at all.
    """
    environments = sorted({env for team in config.teams for env in team.environments})
    if not environments:
        return None
    if len(environments) == 1:
        return f"env:{environments[0]}"
    return f"env:({' OR '.join(environments)})"
