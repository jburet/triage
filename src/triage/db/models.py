"""Triage's own tables, in a dedicated ``triage`` schema.

The Platform's checkpoint, thread and run tables live in the same database but
are managed entirely by LangGraph — Alembic is scoped to this schema precisely
so autogenerate can never see, or drop, them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "triage"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


def _now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now()
    )


class SignalRow(Base, TimestampMixin):
    """Every ingested alert or database tick, with its raw payload (ADR-0012: 90 d).

    The Datadog columns are what makes the poller idempotent and the persistence
    gate measurable: ``external_id`` is the event id the overlapping window
    deduplicates on, and the monitor, group and duration are what one alert
    *cycle* is keyed and judged on (ADR-0017, ADR-0018).
    """

    __tablename__ = "signals"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    feature: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(256), index=True, unique=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    team: Mapped[str | None] = mapped_column(String(128))
    monitor_id: Mapped[int | None] = mapped_column(BigInteger)
    # Not "group": it is a reserved word in SQL, and a column that must be quoted
    # everywhere is a column that will eventually not be.
    firing_group: Mapped[str | None] = mapped_column(Text)
    cycle_key: Mapped[str | None] = mapped_column(String(128), index=True)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (Index("ix_signals_monitor_group", "monitor_id", "firing_group"),)


class PollerWatermarkRow(Base, TimestampMixin):
    """Where the alert poller got to (ADR-0017).

    One row per poller name. The query runs from ``watermark - 2 min`` and
    deduplicates on the event id, so this is a *hint* rather than a cursor that
    has to be exact against Datadog's own ingestion lag.
    """

    __tablename__ = "poller_watermarks"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    watermark: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DiagnosisRow(Base, TimestampMixin):
    """Structured output of F1/F3, stored before ticketing so it survives failures."""

    __tablename__ = "diagnoses"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    signal_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("signals.id"))
    feature: Mapped[str] = mapped_column(String(8))
    service: Mapped[str] = mapped_column(String(128), index=True)
    team: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class TicketRow(Base, TimestampMixin):
    """Mirror of a Jira issue Triage created, plus the state dedup needs."""

    __tablename__ = "tickets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    jira_key: Mapped[str] = mapped_column(String(32), unique=True)
    jira_url: Mapped[str] = mapped_column(Text)
    project: Mapped[str] = mapped_column(String(32))
    team: Mapped[str] = mapped_column(String(128))
    service: Mapped[str] = mapped_column(String(128), index=True)
    summary: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(64), default="Proposed by agent")
    diagnosis_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("diagnoses.id"))

    # Dedup bookkeeping (ADR-0003).
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    last_alerted_occurrence: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("ix_tickets_service_state", "service", "state"),)


class EvaluationRow(Base, TimestampMixin):
    """One row per terminal pipeline run.

    Written on *every* path, including the ones that produce no ticket. The
    roadmap is explicit that without this the investment cannot be justified,
    and a metric that only records successes measures nothing.
    """

    __tablename__ = "evaluations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    diagnosis_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("diagnoses.id"))
    ticket_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("tickets.id"))
    feature: Mapped[str] = mapped_column(String(8))
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    compose_attempts: Mapped[int] = mapped_column(Integer, default=0)
    time_to_ticket_seconds: Mapped[float | None] = mapped_column()

    # Filled later from the Jira webhook, at validation and at closure.
    validated: Mapped[bool | None] = mapped_column()
    reviewer_feedback_validation: Mapped[str | None] = mapped_column(Text)
    reviewer_feedback_closure: Mapped[str | None] = mapped_column(Text)


class AnalysisResultRow(Base, TimestampMixin):
    """Result channel for the sandboxed analysis Jobs (ADR-0009)."""

    __tablename__ = "analysis_results"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_name: Mapped[str] = mapped_column(String(253), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class WorkloadRow(Base, TimestampMixin):
    """Which repository a running service is, derived rather than declared (M6).

    Separate from ``system_map`` because it is keyed on the *service* name the
    cluster uses — one row per customer for the mono-tenant application — while
    the map is keyed on the name a repository says it deploys as. The digest is a
    column of its own so a re-derivation can see that nothing moved without
    reading the payload back.
    """

    __tablename__ = "workloads"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    service: Mapped[str] = mapped_column(String(128), unique=True)
    repository: Mapped[str] = mapped_column(String(128), index=True)
    repo_url: Mapped[str | None] = mapped_column(String(256))
    image: Mapped[str | None] = mapped_column(Text)
    image_digest: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SystemMapRow(Base, TimestampMixin):
    """Discovered cartography from F0. Never hand-edited; config.yaml is for hand-edits."""

    __tablename__ = "system_map"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(256))
    team: Mapped[str | None] = mapped_column(String(128))
    source_commit: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (Index("uq_system_map_kind_name", "kind", "name", unique=True),)
