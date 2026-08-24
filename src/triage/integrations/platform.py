"""Creating runs and crons on the LangGraph Platform (ADR-0011).

The Platform needs an Enterprise licence, which is a procurement dependency and
therefore a thing that can be missing on the day F1 is ready. That is why this is
one narrow client behind a protocol and why its absence is a supported
configuration rather than a failure: with no ``TRIAGE_PLATFORM_URL`` the poller
invokes the same graph in-process, with the same thread id, and the only thing
lost is the queue.

The cron half is what makes F1 run without anyone typing a command: one schedule
over the ``alert_poller`` graph. It lives here rather than in a node because
nothing on a graph may know the Platform exists — a cron is created once, from
``scripts/apply_cron.py``, out of ``deploy/platform/cron-alert-poller.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LaunchedRun:
    assistant_id: str
    thread_id: str


@dataclass(frozen=True)
class Cron:
    """A schedule the Platform holds, as it comes back from a search."""

    cron_id: str
    assistant_id: str
    schedule: str


class PlatformClient(Protocol):
    async def create_run(
        self, *, assistant_id: str, thread_id: str, payload: dict[str, Any]
    ) -> LaunchedRun: ...


class PlatformRestClient:
    """Unverified against a live Platform: nothing has been deployed yet."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        client: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: Any = client

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

    async def crons(self, assistant_id: str, *, limit: int = 100) -> list[Cron]:
        """What the Platform is already scheduled to run for this graph."""
        response = await self._http().post(
            "/runs/crons/search", json={"assistant_id": assistant_id, "limit": limit}
        )
        self._refused(response, f"could not list the crons of {assistant_id}")
        return [
            Cron(
                cron_id=str(item.get("cron_id", "")),
                assistant_id=str(item.get("assistant_id", "")),
                schedule=str(item.get("schedule", "")),
            )
            for item in response.json() or []
        ]

    async def create_cron(
        self,
        *,
        assistant_id: str,
        schedule: str,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Cron:
        """Schedule one graph. Stateless: each tick is its own run, with no thread."""
        body: dict[str, Any] = {
            "assistant_id": assistant_id,
            "schedule": schedule,
            "input": payload or {},
        }
        if metadata:
            body["metadata"] = metadata
        response = await self._http().post("/runs/crons", json=body)
        self._refused(response, f"could not schedule {assistant_id} at {schedule!r}")
        created = response.json() or {}
        return Cron(
            cron_id=str(created.get("cron_id", "")),
            assistant_id=assistant_id,
            schedule=schedule,
        )

    async def delete_cron(self, cron_id: str) -> None:
        response = await self._http().delete(f"/runs/crons/{cron_id}")
        self._refused(response, f"could not delete cron {cron_id}")

    @staticmethod
    def _refused(response: Any, what: str) -> None:
        if response.status_code >= 400:
            raise RuntimeError(f"{what}: {response.status_code} {response.text[:200]}")


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
