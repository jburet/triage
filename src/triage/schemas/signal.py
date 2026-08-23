"""An ingested signal: a Datadog alert, or a daily database tick."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from triage.schemas.common import Feature


class SignalStatus(StrEnum):
    RECEIVED = "received"
    ANALYSING = "analysing"
    DIAGNOSED = "diagnosed"
    TICKETED = "ticketed"
    DISCARDED = "discarded"
    FAILED = "failed"


class Signal(BaseModel):
    signal_id: UUID = Field(default_factory=uuid4)
    feature: Feature
    source: str = Field(description="Where it came from, e.g. 'datadog' or 'db-review-cron'.")
    external_id: str | None = Field(
        default=None, description="Vendor identifier, used to reject replayed webhooks."
    )
    service: str
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: SignalStatus = SignalStatus.RECEIVED
    payload: dict[str, Any] = Field(default_factory=dict, description="Raw vendor payload.")
