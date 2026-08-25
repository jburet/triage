"""One tick of the code-exception poller (ADR-0025, ADR-0011).

Hourly, not per-minute. F1's gate is duration — "is this alert *still* firing
fifteen minutes later" — and an Error Tracking issue has no equivalent: it does
not fire and recover, it accumulates. So the pass runs on the period the volume
gate is measured over, and its window is the hour it is asking about.

Three things happen here, and only three.

**Ask each track once.** The search answers counts and attributes in one call
when it is sent ``include=issue``, so a tick costs one call per configured
track and nothing else. Reading issues individually adds nothing: the detail
endpoint returns exactly the attributes the search already returned, measured
field by field on 2026-08-25.

**Filter in the query, not after it.** The environments Triage watches go into
the Datadog query, so an issue from an environment nobody configured is never
returned. That reads an environment from an ``env:`` tag, which ADR-0017
forbids — for *alerts*, where no usable tag exists. APM events carry one, and
ADR-0025 records the departure.

**Decide nothing else.** An issue that is neither new nor regressed in the
window produces nothing, which over the reference hour was all fifteen of them.
Grouping, the volume gate and the analysis are Phases 2 to 4; this node hands
them a list and a count of what it held back.
"""

from datetime import UTC, datetime, timedelta

import structlog
from langchain_core.runnables import RunnableConfig

from triage.errors.issues import Novelty, environment_filter, not_a_code_exception, novelty
from triage.graphs.state import ErrorPollerState
from triage.integrations.datadog import DatadogError
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorIssue, ErrorTrack, SkippedIssue

log = structlog.get_logger(__name__)

POLLER_NAME = "datadog_error_issues"

OVERLAP = timedelta(minutes=5)
"""Read back past the watermark, because Error Tracking's ingestion lag is unknown.

Larger than F1's two minutes for the same reason F1 has one at all: a cursor
that has to be exact against a lag nobody has measured is a cursor that loses
issues. Re-reading an issue is free — it is neither new nor regressed the second
time, so it produces nothing.
"""

CATCH_UP_LIMIT = timedelta(hours=6)
"""How far back a poller that was down will replay. Six ticks, not a weekend.

An exception first seen thirty hours ago is not news, and replaying two days of
them at once would report the backlog as if it had all just happened — which is
the one-off sweep the plan deliberately left out of scope.
"""


async def poll_error_issues(
    state: ErrorPollerState, config: RunnableConfig | None = None
) -> ErrorPollerState:
    """One tick: ask each track, classify what came back, move the watermark on."""
    deps = deps_from_runnable_config(config)
    now = state.get("now") or datetime.now(UTC)
    result: ErrorPollerState = {
        "now": now,
        "issues_seen": {},
        "new": [],
        "regressed": [],
        "skipped": [],
        "failures": [],
        "unchanged": 0,
    }

    start, skipped_span = await _window(deps, now, state.get("since"))
    window = TimeWindow(start=start, end=now)
    result["window"] = window
    if skipped_span is not None:
        result["skipped_span"] = skipped_span

    query = environment_filter(deps.config)
    if query is None:
        result["failures"].append(
            "no environment is watched by any team in config.yaml, so a query would "
            "have asked for the whole org — nothing was read"
        )
        await deps.repo.set_watermark(POLLER_NAME, now)
        return result
    result["query"] = query

    for track in deps.config.errors.tracks:
        await _read_track(deps, track, query, window, result)

    await deps.repo.set_watermark(POLLER_NAME, now)
    log.info(
        "error_poll",
        new=len(result["new"]),
        regressed=len(result["regressed"]),
        unchanged=result["unchanged"],
        skipped=len(result["skipped"]),
    )
    return result


async def _read_track(
    deps: Deps,
    track: ErrorTrack,
    query: str,
    window: TimeWindow,
    result: ErrorPollerState,
) -> None:
    """One track's issues, sorted into what this tick will look at and what it will not."""
    from triage.errors.issues import parse_issues

    try:
        payload = await deps.datadog.search_error_issues(
            query=query,
            frm=window.start,
            to=window.end,
            track=track.value,
            persona=deps.config.errors.persona.value,
        )
    except DatadogError as failure:
        result["failures"].append(f"the {track.value} track could not be read: {failure}")
        return

    issues = parse_issues(payload, track)
    result["issues_seen"][track] = len(issues)
    for issue in issues:
        _sort(issue, window, result)


def _sort(issue: ErrorIssue, window: TimeWindow, result: ErrorPollerState) -> None:
    """New, regressed, skipped with a reason, or nothing — in that order.

    Novelty is decided before the code-exception rule so that the fifteen issues
    of a quiet hour are counted as unchanged rather than reported as skipped: an
    issue nobody is going to look at is not worth a reason.
    """
    why = novelty(issue, window)
    if why is None:
        result["unchanged"] += 1
        return
    reason = not_a_code_exception(issue)
    if reason is not None:
        result["skipped"].append(
            SkippedIssue(issue_id=issue.issue_id, service=issue.service, reason=reason)
        )
        return
    result["new" if why is Novelty.NEW else "regressed"].append(issue)


async def _window(deps: Deps, now: datetime, since: datetime | None) -> tuple[datetime, str | None]:
    """Where to read from, and what was skipped if the poller was down too long.

    ``since`` is an operator naming the window — ``make run-errors ARGS="--hours
    24"`` — and it is neither clamped nor announced. The catch-up limit exists so
    an hourly cron that was down does not report a weekend of backlog as if it
    had just happened; somebody asking for a day is the opposite of that, and
    clamping them silently is how a backfill reads six hours, misses 32 new
    issues, and still says it succeeded (measured 2026-08-25).
    """
    if since is not None:
        return since, None
    watermark = await deps.repo.get_watermark(POLLER_NAME)
    if watermark is None:
        return now - timedelta(minutes=deps.config.errors.lookback_minutes), None
    if now - watermark <= CATCH_UP_LIMIT:
        return watermark - OVERLAP, None

    start = now - CATCH_UP_LIMIT
    behind = int((now - watermark).total_seconds() // 3600)
    skipped = (
        f"{watermark.isoformat()} .. {start.isoformat()} "
        f"({int((start - watermark).total_seconds() // 3600)} hours)"
    )
    await deps.slack.post(
        channel=deps.config.platform_channel(),
        text=(
            f":hourglass: The code-exception poller was behind by {behind} hours and "
            f"replayed only the last {int(CATCH_UP_LIMIT.total_seconds() // 3600)}. "
            f"Exceptions first seen between {skipped} were not read."
        ),
    )
    return start, skipped
