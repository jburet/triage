"""What F2 collects behind one error group, and what it says when it finds nothing.

Three collectors and one rule. The collectors are the logs behind the group, the
error spans behind it, and the operations those spans ran under. The rule is what
happens when they come back empty, and it is the only interesting part.

**The queries are a reconstruction, and are stated as one.** Datadog exposes no
attribute joining a span or a log back to the Error Tracking issue it was grouped
into — probed 2026-08-25: nine candidate endpoints answered 404, four candidate
include values answered ``invalid include``, four candidate facets returned zero
spans over seven days. So the query is rebuilt from the group's own fields, and a
rebuilt query is a guess about identity that has to be shown to whoever reads the
report (:class:`~triage.schemas.errors.Reconstruction`).

**Empty has three meanings here, not two.** F1 separates "quiet window" from "not
instrumented" by widening in *time* (ADR-0016). F2 cannot: the issue already
proves the exceptions happened inside this window, so time is not the question.
The question is whether what happened is retained, and that is separated by
widening in *predicate* — drop the error clause and ask whether anything at all
is collected for these services. Alive means the events were counted and
discarded (``sampled_away``); dead means nobody collects this signal here
(``not_instrumented``). ADR-0027 has the measurement: 211,179 spans in the
reference hour for a service whose 5,869 counted exceptions returned nothing.

**The stack is never reduced away.** Template-and-count is right for a hundred
lines of the same message and wrong for the one thing F2 exists to show, so the
reduction lifts a complete stack trace out before it counts anything and hands it
back whole. Measured on the org, no service ships one today — ``@error.stack:*``
returns zero spans org-wide and two services ship such logs, neither a tenant —
which makes the rule unobserved, not optional.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from triage.collect import reduce as reducers
from triage.config import CollectionConfig
from triage.integrations.datadog import DatadogClient, DatadogError
from triage.schemas.collection import Collector, CollectorResult, CollectorStatus
from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorCollection, ErrorGroup, Reconstruction

log = structlog.get_logger(__name__)

Fetch = Callable[[str, TimeWindow], Awaitable[dict[str, Any]]]
Reducer = Callable[[dict[str, Any]], dict[str, Any]]

LOGS = "logs"
SPANS = "spans"

STACK_KEYS = ("stack", "stack_trace", "stacktrace")
STACK_HINT = "\n\tat "


def collection_window(now: datetime, lookback_minutes: int) -> TimeWindow:
    """Back from the tick to the configured lookback, and no further (M8 3.4).

    Not the group's ``first_seen``: a defect first seen in March would ask Datadog
    for five months of logs to describe an hour's worth of occurrences, and the
    occurrences the gate counted are this tick's.
    """
    return TimeWindow(start=now - timedelta(minutes=lookback_minutes), end=now)


def _scope(services: Sequence[str]) -> str:
    ordered = sorted(services)
    if not ordered:
        return "*"
    if len(ordered) == 1:
        return f"service:{ordered[0]}"
    return "service:(" + " OR ".join(ordered) + ")"


def reconstruct(group: ErrorGroup) -> Reconstruction:
    """The three queries, built from the group's own fields and nothing else."""
    scope = _scope(list(group.services))
    return Reconstruction(
        narrow=f'{scope} @error.type:"{group.error_type}"',
        broad=f"{scope} status:error",
        control=scope,
    )


# -- reduction -----------------------------------------------------------------


def _flatten(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def stack_of(attributes: dict[str, Any]) -> str | None:
    """One complete stack trace out of one event's attributes, verbatim.

    Datadog nests it differently per source — ``error.stack`` inside a log's inner
    attributes, ``error.stack`` under a span's ``custom`` — and some shippers put
    it in the message with nothing else marking it. All three are looked for, and
    whatever is found is returned uncut: a clipped stack is a stack whose deepest
    frame, the one naming the code, is the part that was thrown away.
    """
    flat = _flatten(attributes)
    for key, value in flat.items():
        if isinstance(value, str) and value.strip() and key.split(".")[-1] in STACK_KEYS:
            return value
    message = flat.get("message")
    if isinstance(message, str) and STACK_HINT in message:
        return message
    return None


def _first_stack(events: Sequence[dict[str, Any]]) -> str | None:
    for event in events:
        found = stack_of(event.get("attributes", {}) or {})
        if found:
            return found
    return None


def reduce_error_logs(
    payload: dict[str, Any], max_templates: int, max_lines: int
) -> dict[str, Any]:
    """F1's log reduction, with one complete stack trace lifted out first (M8 3.1)."""
    reduced = reducers.reduce_logs(payload, max_templates, max_lines)
    stack = _first_stack(payload.get("data", []) or [])
    if stack is not None:
        reduced["stack"] = stack
    return reduced


def reduce_error_spans(payload: dict[str, Any], max_spans: int) -> dict[str, Any]:
    """Enough of a span to open the trace in Datadog, and the stack if it carried one."""
    raw = payload.get("data", []) or []
    spans = []
    for event in raw[:max_spans]:
        attributes = event.get("attributes", {}) or {}
        custom = _flatten(attributes.get("custom", {}) or {})
        spans.append(
            {
                "at": attributes.get("start_timestamp") or attributes.get("timestamp"),
                "trace_id": attributes.get("trace_id") or custom.get("otel.trace_id"),
                "span_id": attributes.get("span_id"),
                "service": attributes.get("service"),
                "operation": attributes.get("operation_name") or custom.get("operation_name"),
                "resource": attributes.get("resource_name"),
                "error_type": custom.get("error.type"),
                "error_message": custom.get("error.message"),
            }
        )
    reduced: dict[str, Any] = {"count": len(raw), "lines": spans}
    stack = _first_stack(raw)
    if stack is not None:
        reduced["stack"] = stack
    return reduced


def _total(reduced: dict[str, Any]) -> int:
    for key in ("buckets", "by"):
        rows = reduced.get(key)
        if isinstance(rows, list) and rows:
            return sum(int(row.get("count") or 0) for row in rows if isinstance(row, dict))
    count = reduced.get("count")
    return int(count) if isinstance(count, int) else 0


# -- the collectors ------------------------------------------------------------


@dataclass(frozen=True)
class Call:
    collector: Collector
    track: str
    fetch: Fetch
    reduce: Reducer


@dataclass(frozen=True)
class Attempt:
    """One collector after its queries were run, before the absence was named."""

    call: Call
    query: str
    payload: dict[str, Any]
    detail: str | None = None
    failure: str | None = None


class Collectors:
    """F2's three, bound to one client and one set of caps."""

    def __init__(self, client: DatadogClient, config: CollectionConfig) -> None:
        self._client = client
        self._config = config

    async def _logs(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.search_logs(query=query, frm=window.start, to=window.end)

    async def _spans(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.search_spans(query=query, frm=window.start, to=window.end)

    async def _span_counts(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.aggregate_spans(
            query=query, frm=window.start, to=window.end, group_by=("resource_name",)
        )

    async def _log_control(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.aggregate_logs(query=query, frm=window.start, to=window.end)

    def _reduce_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        return reduce_error_logs(
            payload, self._config.max_log_templates, self._config.max_log_lines
        )

    def _reduce_spans(self, payload: dict[str, Any]) -> dict[str, Any]:
        return reduce_error_spans(payload, self._config.max_log_lines)

    def calls(self) -> list[Call]:
        return [
            Call(Collector.ERROR_LOGS, LOGS, self._logs, self._reduce_logs),
            Call(Collector.ERROR_SPANS, SPANS, self._spans, self._reduce_spans),
            Call(
                Collector.ERROR_SPAN_COUNTS,
                SPANS,
                self._span_counts,
                reducers.reduce_spans,
            ),
        ]

    async def attempt(self, call: Call, queries: Reconstruction, window: TimeWindow) -> Attempt:
        """The narrow query, then the broad one. Nothing raises out of here (M8 3.5)."""
        try:
            payload = call.reduce(await call.fetch(queries.narrow, window))
        except Exception as exc:
            return self._failed(call, queries.narrow, exc)
        if not reducers.is_empty(payload):
            return Attempt(call, queries.narrow, payload)
        try:
            broadened = call.reduce(await call.fetch(queries.broad, window))
        except Exception as exc:
            return self._failed(call, queries.broad, exc)
        if not reducers.is_empty(broadened):
            return Attempt(
                call,
                queries.broad,
                broadened,
                detail=(
                    f"the group's own exception type matched nothing (`{queries.narrow}`), so "
                    f"this is every error on the same services — it may include errors that "
                    f"are not this defect"
                ),
            )
        return Attempt(call, queries.narrow, payload, detail=_nothing_matched(queries))

    def _failed(self, call: Call, query: str, exc: Exception) -> Attempt:
        if not isinstance(exc, DatadogError):
            log.warning("error_collector_raised", collector=call.collector.value, error=str(exc))
        return Attempt(
            call, query, {}, failure=f"{type(exc).__name__}: {exc}" if _unnamed(exc) else str(exc)
        )

    async def control(self, track: str, query: str, window: TimeWindow) -> int | None:
        """Anything at all for these services? ``None`` when the control itself failed."""
        fetch = self._log_control if track == LOGS else self._span_counts
        reduce = reducers.reduce_log_aggregate if track == LOGS else reducers.reduce_spans
        try:
            return _total(reduce(await fetch(query, window)))
        except Exception as exc:
            log.warning("error_control_failed", track=track, error=str(exc))
            return None


def _unnamed(exc: Exception) -> bool:
    return not isinstance(exc, DatadogError)


def _nothing_matched(queries: Reconstruction) -> str:
    return (
        f"nothing matched `{queries.narrow}`, nor the broader `{queries.broad}`, over the "
        f"collection window"
    )


def _settle(attempt: Attempt, controls: dict[str, int | None], claimed: int) -> CollectorResult:
    """Turn an attempt into a result, naming which kind of nothing it found (ADR-0027)."""
    call = attempt.call
    if attempt.failure is not None:
        return CollectorResult(
            collector=call.collector,
            query=attempt.query,
            status=CollectorStatus.FAILED,
            detail=f"Datadog refused this call: {attempt.failure}",
        )
    if not reducers.is_empty(attempt.payload):
        return CollectorResult(
            collector=call.collector,
            query=attempt.query,
            status=CollectorStatus.OK,
            detail=attempt.detail,
            payload=attempt.payload,
        )
    control = controls.get(call.track)
    if control is None:
        return CollectorResult(
            collector=call.collector,
            query=attempt.query,
            status=CollectorStatus.EMPTY,
            detail=f"{attempt.detail}; the control query could not be run, so whether "
            f"anything is collected for these services is unknown",
            payload=attempt.payload,
        )
    if control > 0 and claimed > 0:
        return CollectorResult(
            collector=call.collector,
            query=attempt.query,
            status=CollectorStatus.SAMPLED_AWAY,
            detail=(
                f"{attempt.detail}. Error Tracking counted {claimed:,} occurrences in this "
                f"window and the same services returned {control:,} {call.track} with the "
                f"error predicate dropped, so the telemetry exists and is being discarded "
                f"before it can be searched — a retention filter keeps error spans, and "
                f"nothing here can recover them"
            ),
            payload=attempt.payload,
        )
    if control > 0:
        return CollectorResult(
            collector=call.collector,
            query=attempt.query,
            status=CollectorStatus.EMPTY,
            detail=f"{attempt.detail}, although the same services returned {control:,} "
            f"{call.track} with the error predicate dropped",
            payload=attempt.payload,
        )
    return CollectorResult(
        collector=call.collector,
        query=attempt.query,
        status=CollectorStatus.NOT_INSTRUMENTED,
        detail=f"{attempt.detail}, and nothing at all for these services either: this "
        f"signal is not collected for them",
        payload=attempt.payload,
    )


async def collect_group(
    client: DatadogClient,
    group: ErrorGroup,
    window: TimeWindow,
    config: CollectionConfig,
) -> ErrorCollection:
    """Every collector, then the control that says what an empty one means."""
    queries = reconstruct(group)
    collectors = Collectors(client, config)
    calls = collectors.calls()
    attempts = list(
        await asyncio.gather(*(collectors.attempt(call, queries, window) for call in calls))
    )
    needed = sorted(
        {
            attempt.call.track
            for attempt in attempts
            if attempt.failure is None and reducers.is_empty(attempt.payload)
        }
    )
    totals = await asyncio.gather(
        *(collectors.control(track, queries.control, window) for track in needed)
    )
    controls = dict(zip(needed, totals, strict=True))
    return ErrorCollection(
        group_key=group.key,
        window=window,
        reconstruction=queries,
        claimed_occurrences=group.occurrences,
        results=[_settle(attempt, controls, group.occurrences) for attempt in attempts],
    )
