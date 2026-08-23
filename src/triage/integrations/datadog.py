"""Datadog, read-only, over REST (ADR-0016, architecture §5).

Six queries, no vendor correlation product: the sweep and the follow-up loop are
built from these and nothing else, which is what makes every fact in a ticket
traceable to a call Triage made.

Rate limits are the interesting part of the real client. Measured from response
headers on 2026-08-23 — spans 5 per 60 s, logs search 3 per 10 s, logs aggregate
2 per 10 s, events and monitors effectively unlimited — they are tight enough
that one sweep spends two of the five span calls, so concurrent incidents
throttle each other. The client therefore *serialises* the scarce endpoints
through a per-endpoint semaphore rather than firing them all and retrying, and
honours ``x-ratelimit-reset`` when it is throttled anyway. Retrying alone would
turn one throttled incident into two.

The credentials are a service account's, not a person's: an application key dies
with the user who owns it, and an F1 that stops collecting the week someone
leaves is worse than no F1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_THROTTLE_WAIT = 30.0

CONCURRENCY: Mapping[str, int] = {
    "spans": 1,
    "logs": 1,
    "events": 4,
    "metrics": 4,
    "monitors": 4,
}
"""Concurrent calls allowed per endpoint family, from the measured limits."""


def _millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class DatadogError(RuntimeError):
    """A call did not come back with data. Never fatal to a collection."""


class DatadogClient(Protocol):
    """Everything F1 is allowed to ask Datadog."""

    async def search_events(
        self, *, query: str, frm: datetime, to: datetime, limit: int = 200
    ) -> dict[str, Any]: ...

    async def get_monitor(self, monitor_id: int) -> dict[str, Any]: ...

    async def query_timeseries(
        self, *, query: str, frm: datetime, to: datetime
    ) -> dict[str, Any]: ...

    async def aggregate_logs(
        self, *, query: str, frm: datetime, to: datetime, group_by: Sequence[str] = ("status",)
    ) -> dict[str, Any]: ...

    async def search_logs(
        self, *, query: str, frm: datetime, to: datetime, limit: int = 60
    ) -> dict[str, Any]: ...

    async def aggregate_spans(
        self, *, query: str, frm: datetime, to: datetime
    ) -> dict[str, Any]: ...


class DatadogRestClient:
    """The real client. Unverified against a live org beyond the captured fixtures."""

    def __init__(
        self,
        site: str,
        api_key: str,
        app_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = f"https://{site}"
        self._headers = {
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        self._client: Any = None
        self._gates = {family: asyncio.Semaphore(size) for family, size in CONCURRENCY.items()}

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers=self._headers, timeout=self._timeout
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, family: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with self._gates[family]:
            response = await self._http().request(method, path, **kwargs)
            if response.status_code == 429:
                pause = min(
                    float(response.headers.get("x-ratelimit-reset", "5")) + 1, MAX_THROTTLE_WAIT
                )
                log.warning("datadog_throttled", path=path, waiting=pause)
                await asyncio.sleep(pause)
                response = await self._http().request(method, path, **kwargs)
        if response.status_code >= 400:
            raise DatadogError(
                f"{method} {path} returned {response.status_code}: {response.text[:200]}"
            )
        body: dict[str, Any] = response.json()
        return body

    async def search_events(
        self, *, query: str, frm: datetime, to: datetime, limit: int = 200
    ) -> dict[str, Any]:
        return await self._call(
            "events",
            "POST",
            "/api/v2/events/search",
            json={
                "filter": {"query": query, "from": _millis(frm), "to": _millis(to)},
                "page": {"limit": limit},
                "sort": "timestamp",
            },
        )

    async def get_monitor(self, monitor_id: int) -> dict[str, Any]:
        return await self._call(
            "monitors", "GET", f"/api/v1/monitor/{monitor_id}", params={"group_states": "all"}
        )

    async def query_timeseries(self, *, query: str, frm: datetime, to: datetime) -> dict[str, Any]:
        return await self._call(
            "metrics",
            "GET",
            "/api/v1/query",
            params={"from": int(frm.timestamp()), "to": int(to.timestamp()), "query": query},
        )

    async def aggregate_logs(
        self, *, query: str, frm: datetime, to: datetime, group_by: Sequence[str] = ("status",)
    ) -> dict[str, Any]:
        return await self._call(
            "logs",
            "POST",
            "/api/v2/logs/analytics/aggregate",
            json={
                "filter": {"query": query, "from": _millis(frm), "to": _millis(to)},
                "compute": [{"aggregation": "count", "type": "total"}],
                "group_by": [{"facet": facet, "limit": 10} for facet in group_by],
            },
        )

    async def search_logs(
        self, *, query: str, frm: datetime, to: datetime, limit: int = 60
    ) -> dict[str, Any]:
        return await self._call(
            "logs",
            "POST",
            "/api/v2/logs/events/search",
            json={
                "filter": {"query": query, "from": _millis(frm), "to": _millis(to)},
                "page": {"limit": limit},
                "sort": "-timestamp",
            },
        )

    async def aggregate_spans(self, *, query: str, frm: datetime, to: datetime) -> dict[str, Any]:
        return await self._call(
            "spans",
            "POST",
            "/api/v2/spans/analytics/aggregate",
            json={
                "data": {
                    "type": "aggregate_request",
                    "attributes": {
                        "filter": {
                            "query": query,
                            "from": frm.isoformat(),
                            "to": to.isoformat(),
                        },
                        "compute": [{"aggregation": "count", "type": "total"}],
                        "group_by": [{"facet": "service", "limit": 10}],
                    },
                }
            },
        )


@dataclass(frozen=True)
class RecordedQuery:
    endpoint: str
    query: str


EMPTY_EVENTS: dict[str, Any] = {"data": []}
EMPTY_LOGS: dict[str, Any] = {"data": []}
EMPTY_SERIES: dict[str, Any] = {"series": []}
EMPTY_AGGREGATE: dict[str, Any] = {"data": []}


@dataclass
class FakeDatadogClient:
    """Replays captured responses, keyed by ``endpoint`` then by a substring of the query.

    Substring rather than exact match because a captured fixture is tied to one
    tenant and one window, while the code under test builds its own queries: what
    a test asserts is that the *right kind* of query went to the right endpoint,
    not that Triage reproduced a hand-written one character for character. An
    endpoint with no matching entry answers empty — which is a real Datadog
    answer, and the one that exercises the emptiness rule (ADR-0016).
    """

    responses: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    fail: Mapping[str, str] = field(default_factory=dict)
    calls: list[RecordedQuery] = field(default_factory=list)

    def _answer(self, endpoint: str, query: str, empty: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(RecordedQuery(endpoint=endpoint, query=query))
        if endpoint in self.fail:
            raise DatadogError(self.fail[endpoint])
        for marker, payload in self.responses.get(endpoint, {}).items():
            if marker in query:
                return dict(payload)
        return empty

    def queries_for(self, endpoint: str) -> list[str]:
        return [call.query for call in self.calls if call.endpoint == endpoint]

    async def search_events(
        self, *, query: str, frm: datetime, to: datetime, limit: int = 200
    ) -> dict[str, Any]:
        return self._answer("events", query, EMPTY_EVENTS)

    async def get_monitor(self, monitor_id: int) -> dict[str, Any]:
        return self._answer("monitor", str(monitor_id), {})

    async def query_timeseries(self, *, query: str, frm: datetime, to: datetime) -> dict[str, Any]:
        return self._answer("metrics", query, EMPTY_SERIES)

    async def aggregate_logs(
        self, *, query: str, frm: datetime, to: datetime, group_by: Sequence[str] = ("status",)
    ) -> dict[str, Any]:
        return self._answer("logs_aggregate", query, {"data": {"buckets": []}})

    async def search_logs(
        self, *, query: str, frm: datetime, to: datetime, limit: int = 60
    ) -> dict[str, Any]:
        return self._answer("logs", query, EMPTY_LOGS)

    async def aggregate_spans(self, *, query: str, frm: datetime, to: datetime) -> dict[str, Any]:
        return self._answer("spans", query, EMPTY_AGGREGATE)
