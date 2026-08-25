"""What F2 collects behind one error group, and what it says when it finds nothing.

Three collectors and one rule. The collectors are the logs behind the group, the
error spans behind it, and the operations those spans ran under. The rule is what
happens when they come back empty, and it is the only interesting part.

**The query is a reconstruction, and the join is not Datadog's.** Nothing joins a
span back to the Error Tracking issue it was grouped into, so the occurrences are
looked for rather than fetched. Where they are looked for changed on 2026-08-25
(ADR-0029): the platform runs the OpenTelemetry agent, so ``@error.type`` is
empty and matches nothing, and the exception's type, message and whole stack are
inside the span attribute ``custom.events``. The query is therefore
``service:<svc> status:error`` and the match is ``exception.type`` inside each
returned span — done here, in Python. Both halves are stated
(:class:`~triage.schemas.errors.Reconstruction`), because a reader who cannot
re-run the search cannot check the finding.

**Empty has four meanings here, not two.** F1 separates "quiet window" from "not
instrumented" by widening in *time* (ADR-0016). F2 cannot: the issue already
proves the exceptions happened inside this window, so time is not the question.
The question is what the sampler kept, and the answers are different sentences:
error spans were retained and one carries this exception (evidence); error spans
were retained and none carries it (this defect's occurrences were discarded);
none at all was retained though the services are alive (the whole error track is
discarded for them); nothing is collected for these services at all
(``not_instrumented``). The control query — the same services with the error
predicate dropped — is what tells the last two apart.

**The stack is never reduced away.** Template-and-count is right for a hundred
lines of the same message and wrong for the one thing F2 exists to show, so the
reduction lifts a complete stack out before it counts anything and hands it back
whole, ``Caused by:`` chain included. Measured on the reference capture: 66 of 80
retained error spans carried one.
"""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from triage.collect import reduce as reducers
from triage.config import CollectionConfig
from triage.errors.otel import ExceptionEvent, exceptions_in
from triage.integrations.datadog import DatadogClient, DatadogError
from triage.schemas.collection import Collector, CollectorResult, CollectorStatus
from triage.schemas.common import TimeWindow
from triage.schemas.errors import (
    ErrorCollection,
    ErrorGroup,
    ExceptionExemplar,
    Reconstruction,
)

log = structlog.get_logger(__name__)

Fetch = Callable[[str, TimeWindow], Awaitable[dict[str, Any]]]
Reducer = Callable[[dict[str, Any]], dict[str, Any]]

LOGS = "logs"
SPANS = "spans"

STACK_KEYS = ("stack", "stack_trace", "stacktrace")
STACK_HINT = "\n\tat "


def collection_window(
    now: datetime, lookback_minutes: int, counted_over: TimeWindow | None = None
) -> TimeWindow:
    """The window this tick counted the occurrences over (M8 3.4, corrected).

    Not the group's ``first_seen``: a defect first seen in March would ask Datadog
    for five months of logs to describe an hour's worth of occurrences, and the
    occurrences the gate counted are this tick's. But it must be *this tick's*, and
    the configured lookback is only that on an hourly tick. A 13-hour backfill run
    live on 2026-08-25 counted a burst between 02:29 and 03:12 and then looked for
    its evidence between 08:41 and 09:41, where there was none: the query and the
    count were about different hours, and the collector reported an absence that was
    an artefact of the window. So the poll window is used when the group carries
    one, and it is bounded by whatever the poller was allowed to catch up over.
    """
    if counted_over is not None:
        return counted_over
    return TimeWindow(start=now - timedelta(minutes=lookback_minutes), end=now)


def _scope(services: Sequence[str]) -> str:
    ordered = sorted(services)
    if not ordered:
        return "*"
    if len(ordered) == 1:
        return f"service:{ordered[0]}"
    return "service:(" + " OR ".join(ordered) + ")"


def reconstruct(group: ErrorGroup) -> Reconstruction:
    """The search, the match and the control, built from the group's own fields."""
    scope = _scope(list(group.services))
    return Reconstruction(
        query=f"{scope} status:error",
        match=f'exception.type:"{group.error_type}" inside each span\'s custom.events',
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


RETAINED = "retained_error_spans"
OTHERS = "other_exception_types"


def reduce_error_spans(payload: dict[str, Any], max_spans: int, error_type: str) -> dict[str, Any]:
    """The spans that carry *this* exception, and a count of the ones that did not.

    The query cannot ask for the exception type — with OpenTelemetry it is not an
    attribute Datadog indexes — so the filter is here, over the parsed span
    events. What does not match is still counted and named: "twenty error spans
    were retained and none of them is this defect" is the finding that separates
    a sampler discarding these occurrences from a service nobody instruments,
    and it is only available on the way past.
    """
    raw = payload.get("data", []) or []
    matched = [event for event in exceptions_in(payload) if event.error_type == error_type]
    others = Counter(
        event.error_type
        for event in exceptions_in(payload)
        if event.error_type and event.error_type != error_type
    )
    reduced: dict[str, Any] = {
        "count": len(matched),
        "lines": [_span_line(event) for event in matched[:max_spans]],
        RETAINED: len(raw),
    }
    if others:
        reduced[OTHERS] = dict(others.most_common())
    stack = next((event.stacktrace for event in matched if event.stacktrace), None)
    if stack is None:
        stack = _first_stack(raw)
    if stack is not None:
        reduced["stack"] = stack
    return reduced


def _span_line(event: ExceptionEvent) -> dict[str, Any]:
    return {
        "at": event.at,
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "service": event.service,
        "operation": event.operation,
        "resource": event.resource,
        "error_type": event.error_type,
        "error_message": event.message,
    }


def exemplar_of(payload: dict[str, Any], error_type: str) -> ExceptionExemplar | None:
    """One occurrence with a stack, or nothing. The report's whole new fact (ADR-0029)."""
    for event in exceptions_in(payload):
        if event.error_type == error_type and event.stacktrace:
            return ExceptionExemplar(
                error_type=event.error_type,
                message=event.message,
                stack=event.stacktrace,
                frames=[frame.located for frame in event.frames],
                trace_id=event.trace_id,
                span_id=event.span_id,
                at=event.at,
                service=event.service,
                operation=event.operation,
                resource=event.resource,
            )
    return None


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
    """One collector after its query was run, before the absence was named."""

    call: Call
    query: str
    payload: dict[str, Any]
    detail: str | None = None
    failure: str | None = None
    raw: dict[str, Any] | None = None
    """What Datadog answered, before the reduction — the exemplar is lifted from it."""


class Collectors:
    """F2's three, bound to one client, one set of caps and one exception type."""

    def __init__(
        self, client: DatadogClient, config: CollectionConfig, error_type: str = ""
    ) -> None:
        self._client = client
        self._config = config
        self._error_type = error_type

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
        return reduce_error_spans(payload, self._config.max_log_lines, self._error_type)

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
        """One query, then the match. Nothing raises out of here (M8 3.5)."""
        try:
            raw = await call.fetch(queries.query, window)
            payload = call.reduce(raw)
        except Exception as exc:
            return self._failed(call, queries.query, exc)
        return Attempt(call, queries.query, payload, detail=_caveat(call, payload), raw=raw)

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


def _caveat(call: Call, payload: dict[str, Any]) -> str | None:
    """What a collector that found something still has to admit about it."""
    if call.collector is Collector.ERROR_SPAN_COUNTS:
        return (
            "counts every error on these services over the window, not only this "
            "exception — the exception type is not an attribute Datadog can filter on here"
        )
    retained = payload.get(RETAINED)
    if call.collector is Collector.ERROR_SPANS and isinstance(retained, int):
        return (
            f"{payload.get('count', 0)} of the {retained} error spans Datadog retained for "
            f"these services carry this exception, matched on `exception.type` inside their "
            f"OpenTelemetry events"
        )
    return None


def _absence(attempt: Attempt, control: int | None, claimed: int) -> tuple[CollectorStatus, str]:
    """Which of the four nothings this is, and the sentence that says so (ADR-0029).

    The order is the order of what a reader can act on. Retained error spans that
    are not this exception name a sampler decision about *this defect*; no
    retained error spans at all name one about the whole service; nothing at all
    names an absent pipeline. Only the last tells a developer to look elsewhere.
    """
    call = attempt.call
    query = attempt.query
    retained = attempt.payload.get(RETAINED)
    others = attempt.payload.get(OTHERS)
    if isinstance(retained, int) and retained > 0:
        named = (
            ", ".join(f"`{name}` {count}" for name, count in others.items())
            if isinstance(others, dict)
            else ""
        )
        carried = f" (they carry {named})" if named else ""
        return CollectorStatus.SAMPLED_AWAY, (
            f"`{query}` returned {retained:,} retained error spans and none of them carries "
            f"this exception{carried}, while Error Tracking counted {claimed:,} occurrences "
            f"of it in the same window — this defect's spans were discarded by the sampler "
            f"before they could be searched"
        )
    if control is None:
        return CollectorStatus.EMPTY, (
            f"nothing matched `{query}` over the collection window; the control query could "
            f"not be run, so whether anything is collected for these services is unknown"
        )
    if control > 0 and claimed > 0:
        return CollectorStatus.SAMPLED_AWAY, (
            f"nothing matched `{query}`, and no error {call.track} were retained for these "
            f"services at all, although they returned {control:,} {call.track} with the error "
            f"predicate dropped and Error Tracking counted {claimed:,} occurrences — the "
            f"whole error track is being discarded here, and only a retention filter can "
            f"bring it back"
        )
    if control > 0:
        return CollectorStatus.EMPTY, (
            f"nothing matched `{query}`, although the same services returned {control:,} "
            f"{call.track} with the error predicate dropped"
        )
    return CollectorStatus.NOT_INSTRUMENTED, (
        f"nothing matched `{query}`, and nothing at all for these services either: this "
        f"signal is not collected for them"
    )


def _settle(attempt: Attempt, controls: dict[str, int | None], claimed: int) -> CollectorResult:
    """Turn an attempt into a result, naming which kind of nothing it found (ADR-0029)."""
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
    status, detail = _absence(attempt, controls.get(call.track), claimed)
    return CollectorResult(
        collector=call.collector,
        query=attempt.query,
        status=status,
        detail=detail,
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
    collectors = Collectors(client, config, group.error_type)
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
        exemplar=_exemplar(attempts, group.error_type),
        results=[_settle(attempt, controls, group.occurrences) for attempt in attempts],
    )


def _exemplar(attempts: Sequence[Attempt], error_type: str) -> ExceptionExemplar | None:
    for attempt in attempts:
        if attempt.call.collector is Collector.ERROR_SPANS and attempt.raw is not None:
            return exemplar_of(attempt.raw, error_type)
    return None
