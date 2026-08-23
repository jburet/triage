"""Running the collectors: the fixed sweep, then the bounded follow-up loop (ADR-0016).

Sweep for breadth, loop for depth. The sweep is deterministic — same alert class,
same calls — because its output is spent as prompt tokens and a collection whose
cost depends on a model's mood cannot be budgeted. The loop is where depth comes
from, and it exists because of one measured moment: the sweep surfaced a
"StatefulSet deployed" event, and it was worth diffing that event *because* the
sweep had surfaced it. Neither half finds that alone.

Nothing here raises. A collector that throws is a *failed* collector inside a
collection that still has the other six, because an incident where the log
endpoint was throttled is still an incident with events, metrics and a diff.

Emptiness is not a result. A query that returns nothing in the incident window is
re-run — *the same query*, over seven days — before it is written down: empty in
both means this signal is not collected for this scope at all, empty only in the
window means the absence is itself evidence. The reference incident returned no
spans either way (the tenant has no APM), and only the wider query separates that
from "the service was down".

The widening is in time and not in scope, which is a correction the first live
run forced. Widening to the namespace answers a different question — "is anything
collected in this namespace" — and on a real alert it labelled a `service:` tag
that does not exist as "the absence is about this incident", because the
namespace around it was busy. The same query over more time cannot make that
mistake.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog

from triage.collect import reduce as reducers
from triage.collect.recipes import RECIPES, metric_queries, monitor_query_plan
from triage.config import CollectionConfig
from triage.integrations.datadog import DatadogClient, DatadogError
from triage.schemas.alert import Alert
from triage.schemas.collection import (
    AlertClass,
    Collector,
    CollectorResult,
    CollectorStatus,
    FollowUpRequest,
)
from triage.schemas.common import TimeWindow

log = structlog.get_logger(__name__)

Fetch = Callable[[str, TimeWindow], Awaitable[dict[str, Any]]]
Reducer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Call:
    collector: Collector
    query: str
    fetch: Fetch
    reduce: Reducer
    widenable: bool = True
    note: str | None = None


class Collectors:
    """Every collector, bound to one client, one alert and one set of caps."""

    def __init__(self, client: DatadogClient, alert: Alert, config: CollectionConfig) -> None:
        self._client = client
        self._alert = alert
        self._config = config

    # -- the raw fetches, one per Datadog endpoint -------------------------------

    async def _events(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.search_events(query=query, frm=window.start, to=window.end)

    async def _logs(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.search_logs(query=query, frm=window.start, to=window.end)

    async def _logs_aggregate(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.aggregate_logs(query=query, frm=window.start, to=window.end)

    async def _timeseries(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.query_timeseries(query=query, frm=window.start, to=window.end)

    async def _spans(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.aggregate_spans(query=query, frm=window.start, to=window.end)

    async def _monitor(self, query: str, window: TimeWindow) -> dict[str, Any]:
        return await self._client.get_monitor(int(query))

    # -- scopes ------------------------------------------------------------------

    @property
    def _service_scope(self) -> str | None:
        service = self._alert.scope.service or self._alert.scope.stateful_set
        return f"service:{service}" if service else None

    @property
    def _namespace_scope(self) -> str | None:
        namespace = self._alert.scope.namespace
        return f"kube_namespace:{namespace}" if namespace else None

    def _reduce_events(self, payload: dict[str, Any]) -> dict[str, Any]:
        return reducers.reduce_events(payload, self._config.max_events)

    def _reduce_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        return reducers.reduce_logs(
            payload, self._config.max_log_templates, self._config.max_log_lines
        )

    def _reduce_timeseries(self, payload: dict[str, Any]) -> dict[str, Any]:
        return reducers.reduce_timeseries(
            payload, self._config.max_timeseries_series, self._config.max_timeseries_points
        )

    def call_for(self, collector: Collector, query: str) -> Call:
        """One collector, run with an arbitrary query — the follow-up loop's entry point."""
        if collector is Collector.LOGS_SAMPLE:
            return Call(collector, query, self._logs, self._reduce_logs)
        if collector is Collector.LOGS_AGGREGATE:
            return Call(collector, query, self._logs_aggregate, reducers.reduce_log_aggregate)
        if collector is Collector.METRICS:
            return Call(collector, query, self._timeseries, self._reduce_timeseries)
        if collector is Collector.SPANS:
            return Call(collector, query, self._spans, reducers.reduce_spans)
        if collector is Collector.MONITOR_DEFINITION:
            return Call(collector, query, self._monitor, reducers.reduce_monitor, widenable=False)
        return Call(collector, query, self._events, self._reduce_events)

    # -- the sweep ---------------------------------------------------------------

    def _monitor_query_call(self) -> Call:
        plan = monitor_query_plan(self._alert.monitor_query, self._alert.scope)
        if plan is None:
            return Call(
                Collector.MONITOR_QUERY,
                self._alert.monitor_query or "",
                self._events,
                self._reduce_events,
                note="the monitor's query has no re-runnable form, so what it measured "
                "could not be replayed",
            )
        if plan.idiom == "timeseries":
            return Call(
                Collector.MONITOR_QUERY, plan.query, self._timeseries, self._reduce_timeseries
            )
        if plan.idiom == "logs":
            return Call(Collector.MONITOR_QUERY, plan.query, self._logs, self._reduce_logs)
        return Call(Collector.MONITOR_QUERY, plan.query, self._events, self._reduce_events)

    def plan(self, alert_class: AlertClass) -> list[Call]:
        """The recipe's calls, with the scopes this alert actually carries."""
        recipe = RECIPES[alert_class]
        service, namespace = self._service_scope, self._namespace_scope
        logs_scope = service or namespace
        calls: list[Call] = []
        for collector in recipe.collectors:
            if collector is Collector.MONITOR_QUERY:
                calls.append(self._monitor_query_call())
            elif collector is Collector.EVENTS_SERVICE and service:
                calls.append(self.call_for(collector, service))
            elif collector is Collector.EVENTS_NAMESPACE and namespace:
                calls.append(self.call_for(collector, namespace))
            elif (
                (collector is Collector.LOGS_AGGREGATE and logs_scope)
                or (collector is Collector.LOGS_SAMPLE and logs_scope)
                or (collector is Collector.SPANS and logs_scope)
            ):
                calls.append(self.call_for(collector, logs_scope))
            elif collector is Collector.METRICS:
                calls.extend(
                    self.call_for(collector, query)
                    for query in metric_queries(alert_class, self._alert.scope)
                )
        return calls

    # -- execution ---------------------------------------------------------------

    async def execute(self, call: Call, window: TimeWindow) -> CollectorResult:
        if call.note is not None:
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.SKIPPED,
                detail=call.note,
            )
        try:
            raw = await call.fetch(call.query, window)
        except DatadogError as exc:
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.FAILED,
                detail=str(exc),
            )
        except Exception as exc:
            log.warning("collector_raised", collector=call.collector.value, error=str(exc))
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.FAILED,
                detail=f"{type(exc).__name__}: {exc}",
            )

        payload = call.reduce(raw)
        if not reducers.is_empty(payload):
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.OK,
                payload=payload,
            )
        return await self._disambiguate_empty(call, window, payload)

    async def _disambiguate_empty(
        self, call: Call, window: TimeWindow, payload: dict[str, Any]
    ) -> CollectorResult:
        """Nothing came back. Widen before deciding what that means (ADR-0016)."""
        if not call.widenable:
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.EMPTY,
                detail="nothing in the incident window; this query has no wider form to "
                "check it against",
                payload=payload,
            )
        wider = TimeWindow(
            start=window.end - timedelta(days=self._config.widen_days), end=window.end
        )
        try:
            raw = await call.fetch(call.query, wider)
        except Exception as exc:
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.EMPTY,
                detail=f"nothing in the incident window; the widened check failed: {exc}",
                payload=payload,
            )
        if reducers.is_empty(call.reduce(raw)):
            return CollectorResult(
                collector=call.collector,
                query=call.query,
                status=CollectorStatus.NOT_INSTRUMENTED,
                detail=f"nothing in the incident window and nothing for the same query "
                f"over {self._config.widen_days} days: this signal is not collected for "
                f"this scope at all",
                payload=payload,
            )
        return CollectorResult(
            collector=call.collector,
            query=call.query,
            status=CollectorStatus.EMPTY,
            detail=f"nothing in the incident window, although the same query returns data "
            f"over {self._config.widen_days} days: the absence is about this incident",
            payload=payload,
        )


async def sweep(
    client: DatadogClient,
    alert: Alert,
    alert_class: AlertClass,
    window: TimeWindow,
    config: CollectionConfig,
) -> list[CollectorResult]:
    """The recipe's collectors, concurrently. Per-endpoint limits live in the client."""
    collectors = Collectors(client, alert, config)
    calls = collectors.plan(alert_class)
    return list(await asyncio.gather(*(collectors.execute(call, window) for call in calls)))


async def follow_up(
    client: DatadogClient,
    alert: Alert,
    requests: list[FollowUpRequest],
    window: TimeWindow,
    config: CollectionConfig,
    *,
    already_spent: int = 0,
) -> tuple[list[CollectorResult], list[str]]:
    """Run what the analysis tier asked for, within the budget and within the set.

    Both refusals are recorded rather than silently dropped: a request beyond the
    budget is a fact about the budget, and a request for a collector that does
    not exist is a fact about the prompt.
    """
    collectors = Collectors(client, alert, config)
    results: list[CollectorResult] = []
    refused: list[str] = []
    spent = already_spent
    for request in requests:
        if spent >= config.max_followup_calls:
            refused.append(
                f"{request.collector} `{request.query}`: the follow-up budget of "
                f"{config.max_followup_calls} calls was already spent"
            )
            continue
        try:
            collector = Collector(request.collector)
        except ValueError:
            refused.append(
                f"{request.collector!r} is not a collector Triage has; the request "
                f"(`{request.query}`) was discarded"
            )
            continue
        spent += 1
        results.append(
            await collectors.execute(collectors.call_for(collector, request.query), window)
        )
    return results, refused
