"""Persistence seen from the graph's side.

Nodes depend on :class:`TriageRepository`, never on the ORM, so the whole
pipeline runs against :class:`InMemoryRepository` in tests with no database and
no behavioural difference in the branches that matter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triage.db.models import (
    AnalysisResultRow,
    DiagnosisRow,
    EvaluationRow,
    PollerWatermarkRow,
    SignalRow,
    SystemMapRow,
    TicketRow,
)
from triage.schemas.analysis import AnalysisKind, AnalysisStatus
from triage.schemas.common import Feature
from triage.schemas.diagnosis import Diagnosis
from triage.schemas.signal import Signal, SignalStatus
from triage.schemas.system_map import ServiceEntry, SystemMapKind
from triage.schemas.ticket import PipelineOutcome

OPEN_TICKET_STATES = ("Proposed by agent", "Validated", "In progress")

OPEN_SIGNAL_STATES = (
    SignalStatus.RECEIVED,
    SignalStatus.WAITING,
    SignalStatus.ANALYSING,
    SignalStatus.DIAGNOSED,
)
"""Cycles the poller still has to decide about. Everything else is settled."""


@dataclass(frozen=True)
class TicketRecord:
    """A ticket as the pipeline needs it: enough to dedup against and to update."""

    id: UUID
    jira_key: str
    jira_url: str
    service: str
    summary: str
    state: str
    occurrence_count: int
    last_alerted_occurrence: int


@dataclass(frozen=True)
class AnalysisResultRecord:
    """The row an analysis Job writes, as the runner reads it (ADR-0009).

    The Job reaches this table with its own narrow role; Triage only ever reads
    what arrives, which is why the payload stays a plain mapping until the
    per-kind schema admits it.
    """

    job_name: str
    kind: AnalysisKind
    status: AnalysisStatus
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class SystemMapEntry:
    """One row of the map: what it is, who owns it, and the commit it was read from.

    ``team`` is nullable because ownership comes from ``config.yaml`` and a repo
    can be summarised before anyone declares a team for it. That is a real
    absence rather than an unfilled answer, so it is ``None`` and not an
    :class:`~triage.schemas.common.Unknown`.
    """

    kind: SystemMapKind
    name: str
    team: str | None
    source_commit: str | None
    payload: dict[str, Any]


class TriageRepository(Protocol):
    async def save_signal(self, signal: Signal) -> Signal: ...

    async def signal_by_external_id(self, external_id: str) -> Signal | None: ...

    async def signals_for_cycle(
        self, monitor_id: int | None, group: str | None
    ) -> list[Signal]: ...

    async def open_signals(self) -> list[Signal]: ...

    async def update_signal(self, signal: Signal) -> Signal: ...

    async def get_watermark(self, name: str) -> datetime | None: ...

    async def set_watermark(self, name: str, moment: datetime) -> None: ...

    async def open_tickets_for_service(self, service: str) -> list[TicketRecord]: ...

    async def get_ticket(self, jira_key: str) -> TicketRecord | None: ...

    async def save_diagnosis(self, diagnosis: Diagnosis) -> UUID: ...

    async def save_ticket(
        self,
        *,
        jira_key: str,
        jira_url: str,
        project: str,
        team: str,
        service: str,
        summary: str,
        diagnosis_id: UUID | None,
    ) -> TicketRecord: ...

    async def bump_occurrence(self, jira_key: str) -> TicketRecord: ...

    async def mark_alerted(self, jira_key: str) -> TicketRecord: ...

    async def save_evaluation(
        self,
        *,
        feature: Feature,
        outcome: PipelineOutcome,
        diagnosis_id: UUID | None,
        ticket_id: UUID | None,
        compose_attempts: int,
        time_to_ticket_seconds: float | None,
    ) -> None: ...

    async def analysis_result(self, job_name: str) -> AnalysisResultRecord | None: ...

    async def save_analysis_result(
        self,
        *,
        job_name: str,
        kind: AnalysisKind,
        status: AnalysisStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AnalysisResultRecord: ...

    async def upsert_system_map_entries(self, entries: Sequence[SystemMapEntry]) -> int: ...

    async def system_map_for_service(self, service: str) -> ServiceEntry | None: ...

    async def last_summarised_commit(self, repo_url: str) -> str | None: ...

    async def advance_source_commit(self, repo_url: str, commit: str) -> int: ...


def _to_record(row: TicketRow) -> TicketRecord:
    return TicketRecord(
        id=row.id,
        jira_key=row.jira_key,
        jira_url=row.jira_url,
        service=row.service,
        summary=row.summary,
        state=row.state,
        occurrence_count=row.occurrence_count,
        last_alerted_occurrence=row.last_alerted_occurrence,
    )


def _to_signal(row: SignalRow) -> Signal:
    return Signal(
        signal_id=row.id,
        feature=Feature(row.feature),
        source=row.source,
        external_id=row.external_id,
        service=row.service,
        team=row.team,
        monitor_id=row.monitor_id,
        group=row.firing_group,
        cycle_key=row.cycle_key,
        fired_at=row.fired_at,
        recovered_at=row.recovered_at,
        duration_seconds=row.duration_seconds,
        received_at=row.created_at,
        status=SignalStatus(row.status),
        payload=row.payload,
    )


def _signal_columns(signal: Signal) -> dict[str, Any]:
    return {
        "feature": signal.feature.value,
        "source": signal.source,
        "external_id": signal.external_id,
        "service": signal.service,
        "team": signal.team,
        "monitor_id": signal.monitor_id,
        "firing_group": signal.group,
        "cycle_key": signal.cycle_key,
        "fired_at": signal.fired_at,
        "recovered_at": signal.recovered_at,
        "duration_seconds": signal.duration_seconds,
        "status": signal.status.value,
        "payload": signal.payload,
    }


class SqlRepository:
    """PostgreSQL-backed implementation."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def save_signal(self, signal: Signal) -> Signal:
        async with self._sessionmaker() as session, session.begin():
            await session.merge(SignalRow(id=signal.signal_id, **_signal_columns(signal)))
        return signal

    async def update_signal(self, signal: Signal) -> Signal:
        return await self.save_signal(signal)

    async def signal_by_external_id(self, external_id: str) -> Signal | None:
        async with self._sessionmaker() as session:
            row = await session.scalar(
                select(SignalRow).where(SignalRow.external_id == external_id)
            )
        return _to_signal(row) if row else None

    async def signals_for_cycle(self, monitor_id: int | None, group: str | None) -> list[Signal]:
        stmt = (
            select(SignalRow)
            .where(SignalRow.monitor_id == monitor_id, SignalRow.firing_group == group)
            .order_by(SignalRow.created_at.desc())
            .limit(50)
        )
        async with self._sessionmaker() as session:
            rows = (await session.scalars(stmt)).all()
        return [_to_signal(row) for row in rows]

    async def open_signals(self) -> list[Signal]:
        stmt = select(SignalRow).where(
            SignalRow.status.in_([status.value for status in OPEN_SIGNAL_STATES])
        )
        async with self._sessionmaker() as session:
            rows = (await session.scalars(stmt)).all()
        return [_to_signal(row) for row in rows]

    async def get_watermark(self, name: str) -> datetime | None:
        async with self._sessionmaker() as session:
            moment: datetime | None = await session.scalar(
                select(PollerWatermarkRow.watermark).where(PollerWatermarkRow.name == name)
            )
        return moment

    async def set_watermark(self, name: str, moment: datetime) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.merge(PollerWatermarkRow(name=name, watermark=moment))

    async def open_tickets_for_service(self, service: str) -> list[TicketRecord]:
        stmt = (
            select(TicketRow)
            .where(TicketRow.service == service, TicketRow.state.in_(OPEN_TICKET_STATES))
            .order_by(TicketRow.updated_at.desc())
            .limit(25)
        )
        async with self._sessionmaker() as session:
            rows = (await session.scalars(stmt)).all()
        return [_to_record(row) for row in rows]

    async def get_ticket(self, jira_key: str) -> TicketRecord | None:
        async with self._sessionmaker() as session:
            row = await session.scalar(select(TicketRow).where(TicketRow.jira_key == jira_key))
        return _to_record(row) if row else None

    async def save_diagnosis(self, diagnosis: Diagnosis) -> UUID:
        row = DiagnosisRow(
            id=diagnosis.diagnosis_id,
            signal_id=diagnosis.signal_id,
            feature=diagnosis.feature.value,
            service=diagnosis.service,
            team=diagnosis.team,
            confidence=diagnosis.confidence.value,
            payload=diagnosis.model_dump(mode="json"),
        )
        async with self._sessionmaker() as session, session.begin():
            await session.merge(row)
        return diagnosis.diagnosis_id

    async def save_ticket(
        self,
        *,
        jira_key: str,
        jira_url: str,
        project: str,
        team: str,
        service: str,
        summary: str,
        diagnosis_id: UUID | None,
    ) -> TicketRecord:
        row = TicketRow(
            jira_key=jira_key,
            jira_url=jira_url,
            project=project,
            team=team,
            service=service,
            summary=summary,
            diagnosis_id=diagnosis_id,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            record = _to_record(row)
        return record

    async def _mutate(self, jira_key: str, *, alerted: bool) -> TicketRecord:
        async with self._sessionmaker() as session, session.begin():
            row = await session.scalar(select(TicketRow).where(TicketRow.jira_key == jira_key))
            if row is None:
                raise KeyError(f"no ticket {jira_key!r} on record")
            if alerted:
                row.last_alerted_occurrence = row.occurrence_count
            else:
                row.occurrence_count += 1
            await session.flush()
            return _to_record(row)

    async def bump_occurrence(self, jira_key: str) -> TicketRecord:
        return await self._mutate(jira_key, alerted=False)

    async def mark_alerted(self, jira_key: str) -> TicketRecord:
        return await self._mutate(jira_key, alerted=True)

    async def save_evaluation(
        self,
        *,
        feature: Feature,
        outcome: PipelineOutcome,
        diagnosis_id: UUID | None,
        ticket_id: UUID | None,
        compose_attempts: int,
        time_to_ticket_seconds: float | None,
    ) -> None:
        row = EvaluationRow(
            feature=feature.value,
            outcome=outcome.value,
            diagnosis_id=diagnosis_id,
            ticket_id=ticket_id,
            compose_attempts=compose_attempts,
            time_to_ticket_seconds=time_to_ticket_seconds,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)

    async def analysis_result(self, job_name: str) -> AnalysisResultRecord | None:
        async with self._sessionmaker() as session:
            row = await session.scalar(
                select(AnalysisResultRow).where(AnalysisResultRow.job_name == job_name)
            )
        return _to_analysis_record(row) if row else None

    async def save_analysis_result(
        self,
        *,
        job_name: str,
        kind: AnalysisKind,
        status: AnalysisStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AnalysisResultRecord:
        async with self._sessionmaker() as session, session.begin():
            row = await session.scalar(
                select(AnalysisResultRow).where(AnalysisResultRow.job_name == job_name)
            )
            if row is None:
                row = AnalysisResultRow(job_name=job_name, kind=kind.value, status=status.value)
                session.add(row)
            row.kind = kind.value
            row.status = status.value
            row.result = result
            row.error = error
            await session.flush()
            return _to_analysis_record(row)

    async def upsert_system_map_entries(self, entries: Sequence[SystemMapEntry]) -> int:
        async with self._sessionmaker() as session, session.begin():
            for entry in entries:
                row = await session.scalar(
                    select(SystemMapRow).where(
                        SystemMapRow.kind == entry.kind.value, SystemMapRow.name == entry.name
                    )
                )
                if row is None:
                    row = SystemMapRow(kind=entry.kind.value, name=entry.name)
                    session.add(row)
                row.team = entry.team
                row.source_commit = entry.source_commit
                row.payload = entry.payload
            await session.flush()
        return len(entries)

    async def system_map_for_service(self, service: str) -> ServiceEntry | None:
        async with self._sessionmaker() as session:
            row = await session.scalar(
                select(SystemMapRow).where(
                    SystemMapRow.kind == SystemMapKind.SERVICE.value,
                    SystemMapRow.name == service,
                )
            )
        return ServiceEntry.model_validate(row.payload) if row else None

    async def last_summarised_commit(self, repo_url: str) -> str | None:
        stmt = (
            select(SystemMapRow.source_commit)
            .where(
                SystemMapRow.payload["repo_url"].astext == repo_url,
                SystemMapRow.source_commit.is_not(None),
            )
            .order_by(SystemMapRow.updated_at.desc())
            .limit(1)
        )
        async with self._sessionmaker() as session:
            return await session.scalar(stmt)

    async def advance_source_commit(self, repo_url: str, commit: str) -> int:
        stmt = select(SystemMapRow).where(SystemMapRow.payload["repo_url"].astext == repo_url)
        async with self._sessionmaker() as session, session.begin():
            rows = list((await session.scalars(stmt)).all())
            for row in rows:
                row.source_commit = commit
                row.payload = {**row.payload, "source_commit": commit}
            await session.flush()
        return len(rows)


def _to_analysis_record(row: AnalysisResultRow) -> AnalysisResultRecord:
    return AnalysisResultRecord(
        job_name=row.job_name,
        kind=AnalysisKind(row.kind),
        status=AnalysisStatus(row.status),
        result=row.result,
        error=row.error,
    )


@dataclass(frozen=True)
class EvaluationRecord:
    feature: Feature
    outcome: PipelineOutcome
    diagnosis_id: UUID | None
    ticket_id: UUID | None
    compose_attempts: int
    time_to_ticket_seconds: float | None
    recorded_at: datetime


@dataclass
class InMemoryRepository:
    """Test and dry-run double. Same semantics, no database."""

    signals: dict[UUID, Signal] = field(default_factory=dict)
    watermarks: dict[str, datetime] = field(default_factory=dict)
    tickets: dict[str, TicketRecord] = field(default_factory=dict)
    diagnoses: dict[UUID, Diagnosis] = field(default_factory=dict)
    evaluations: list[EvaluationRecord] = field(default_factory=list)
    analysis_results: dict[str, AnalysisResultRecord] = field(default_factory=dict)
    system_map: dict[tuple[SystemMapKind, str], SystemMapEntry] = field(default_factory=dict)

    async def save_signal(self, signal: Signal) -> Signal:
        self.signals[signal.signal_id] = signal
        return signal

    async def update_signal(self, signal: Signal) -> Signal:
        return await self.save_signal(signal)

    async def signal_by_external_id(self, external_id: str) -> Signal | None:
        return next(
            (signal for signal in self.signals.values() if signal.external_id == external_id),
            None,
        )

    async def signals_for_cycle(self, monitor_id: int | None, group: str | None) -> list[Signal]:
        return [
            signal
            for signal in self.signals.values()
            if signal.monitor_id == monitor_id and signal.group == group
        ]

    async def open_signals(self) -> list[Signal]:
        return [signal for signal in self.signals.values() if signal.status in OPEN_SIGNAL_STATES]

    async def get_watermark(self, name: str) -> datetime | None:
        return self.watermarks.get(name)

    async def set_watermark(self, name: str, moment: datetime) -> None:
        self.watermarks[name] = moment

    async def open_tickets_for_service(self, service: str) -> list[TicketRecord]:
        return [
            ticket
            for ticket in self.tickets.values()
            if ticket.service == service and ticket.state in OPEN_TICKET_STATES
        ]

    async def get_ticket(self, jira_key: str) -> TicketRecord | None:
        return self.tickets.get(jira_key)

    async def save_diagnosis(self, diagnosis: Diagnosis) -> UUID:
        self.diagnoses[diagnosis.diagnosis_id] = diagnosis
        return diagnosis.diagnosis_id

    async def save_ticket(
        self,
        *,
        jira_key: str,
        jira_url: str,
        project: str,
        team: str,
        service: str,
        summary: str,
        diagnosis_id: UUID | None,
    ) -> TicketRecord:
        record = TicketRecord(
            id=uuid4(),
            jira_key=jira_key,
            jira_url=jira_url,
            service=service,
            summary=summary,
            state="Proposed by agent",
            occurrence_count=1,
            last_alerted_occurrence=1,
        )
        self.tickets[jira_key] = record
        return record

    def _current(self, jira_key: str) -> TicketRecord:
        try:
            return self.tickets[jira_key]
        except KeyError as exc:
            raise KeyError(f"no ticket {jira_key!r} on record") from exc

    async def bump_occurrence(self, jira_key: str) -> TicketRecord:
        current = self._current(jira_key)
        updated = replace(current, occurrence_count=current.occurrence_count + 1)
        self.tickets[jira_key] = updated
        return updated

    async def mark_alerted(self, jira_key: str) -> TicketRecord:
        current = self._current(jira_key)
        updated = replace(current, last_alerted_occurrence=current.occurrence_count)
        self.tickets[jira_key] = updated
        return updated

    async def save_evaluation(
        self,
        *,
        feature: Feature,
        outcome: PipelineOutcome,
        diagnosis_id: UUID | None,
        ticket_id: UUID | None,
        compose_attempts: int,
        time_to_ticket_seconds: float | None,
    ) -> None:
        self.evaluations.append(
            EvaluationRecord(
                feature=feature,
                outcome=outcome,
                diagnosis_id=diagnosis_id,
                ticket_id=ticket_id,
                compose_attempts=compose_attempts,
                time_to_ticket_seconds=time_to_ticket_seconds,
                recorded_at=datetime.now(UTC),
            )
        )

    async def analysis_result(self, job_name: str) -> AnalysisResultRecord | None:
        return self.analysis_results.get(job_name)

    async def save_analysis_result(
        self,
        *,
        job_name: str,
        kind: AnalysisKind,
        status: AnalysisStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AnalysisResultRecord:
        record = AnalysisResultRecord(
            job_name=job_name, kind=kind, status=status, result=result, error=error
        )
        self.analysis_results[job_name] = record
        return record

    async def upsert_system_map_entries(self, entries: Sequence[SystemMapEntry]) -> int:
        for entry in entries:
            self.system_map[(entry.kind, entry.name)] = entry
        return len(entries)

    async def system_map_for_service(self, service: str) -> ServiceEntry | None:
        entry = self.system_map.get((SystemMapKind.SERVICE, service))
        return ServiceEntry.model_validate(entry.payload) if entry else None

    async def last_summarised_commit(self, repo_url: str) -> str | None:
        for entry in reversed(list(self.system_map.values())):
            if entry.payload.get("repo_url") == repo_url and entry.source_commit:
                return entry.source_commit
        return None

    async def advance_source_commit(self, repo_url: str, commit: str) -> int:
        moved = 0
        for key, entry in self.system_map.items():
            if entry.payload.get("repo_url") != repo_url:
                continue
            self.system_map[key] = replace(
                entry,
                source_commit=commit,
                payload={**entry.payload, "source_commit": commit},
            )
            moved += 1
        return moved
