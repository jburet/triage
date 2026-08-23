"""Creating runs on the LangGraph Platform (ADR-0011).

The Platform needs an Enterprise licence, which is a procurement dependency and
therefore a thing that can be missing on the day F1 is ready. That is why this is
one narrow client behind a protocol and why its absence is a supported
configuration rather than a failure: with no ``TRIAGE_PLATFORM_URL`` the poller
invokes the same graph in-process, with the same thread id, and the only thing
lost is the queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LaunchedRun:
    assistant_id: str
    thread_id: str


class PlatformClient(Protocol):
    async def create_run(
        self, *, assistant_id: str, thread_id: str, payload: dict[str, Any]
    ) -> LaunchedRun: ...


class PlatformRestClient:
    """Unverified against a live Platform: nothing has been deployed yet."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: Any = None

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["x-api-key"] = self._api_key
            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers=headers, timeout=self._timeout
            )
        return self._client

    async def create_run(
        self, *, assistant_id: str, thread_id: str, payload: dict[str, Any]
    ) -> LaunchedRun:
        response = await self._http().post(
            f"/threads/{thread_id}/runs",
            json={"assistant_id": assistant_id, "input": payload},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"the Platform refused a run for {thread_id}: "
                f"{response.status_code} {response.text[:200]}"
            )
        return LaunchedRun(assistant_id=assistant_id, thread_id=thread_id)


@dataclass
class FakePlatformClient:
    runs: list[LaunchedRun] = field(default_factory=list)
    payloads: list[dict[str, Any]] = field(default_factory=list)

    async def create_run(
        self, *, assistant_id: str, thread_id: str, payload: dict[str, Any]
    ) -> LaunchedRun:
        run = LaunchedRun(assistant_id=assistant_id, thread_id=thread_id)
        self.runs.append(run)
        self.payloads.append(payload)
        return run
