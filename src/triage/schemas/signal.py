"""An ingested signal: a Datadog alert, or a daily database tick."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from triage.schemas.common import Feature


class SignalStatus(StrEnum):
    """The life of one alert cycle.

    ``waiting`` and ``self_recovered`` are the persistence gate (ADR-0018): an
    alert is not analysed when it fires, it is analysed once it has still been
    firing N minutes later, and the majority that heal before then are recorded
    with their duration and never analysed. ``out_of_scope`` is the other terminal
    state a signal can reach without ever being looked at — an alert that resolves
    to no configured team (ADR-0017).
    """

    RECEIVED = "received"
    WAITING = "waiting"
    ANALYSING = "analysing"
    DIAGNOSED = "diagnosed"
    TICKETED = "ticketed"
    DISCARDED = "discarded"
    SELF_RECOVERED = "self_recovered"
    OUT_OF_SCOPE = "out_of_scope"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset(
    {
        SignalStatus.TICKETED,
        SignalStatus.DISCARDED,
        SignalStatus.SELF_RECOVERED,
        SignalStatus.OUT_OF_SCOPE,
        SignalStatus.FAILED,
    }
)


class Signal(BaseModel):
    signal_id: UUID = Field(default_factory=uuid4)
    feature: Feature
    source: str = Field(description="Where it came from, e.g. 'datadog' or 'db-review-cron'.")
    external_id: str | None = Field(
        default=None,
        description="Vendor identifier — the Datadog event id — which is what makes the "
        "poller's overlapping windows idempotent (ADR-0017).",
    )
    service: str
    team: str | None = Field(default=None, description="None when nothing owns it.")
    monitor_id: int | None = None
    group: str | None = Field(default=None, description="The firing group, one cycle per group.")
    cycle_key: str | None = Field(
        default=None, description="Datadog's key for one firing cycle; re-notifications share it."
    )
    fired_at: datetime | None = None
    recovered_at: datetime | None = None
    duration_seconds: float | None = Field(
        default=None,
        description="How long the cycle was firing. What the flap rule counts, and what "
        "the post-mortem timeline needs anyway (ADR-0018).",
    )
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: SignalStatus = SignalStatus.RECEIVED
    payload: dict[str, Any] = Field(default_factory=dict, description="Raw vendor payload.")
