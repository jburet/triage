"""Persistence seen from the graph's side.

Nodes depend on :class:`TriageRepository`, never on the ORM, so the whole
pipeline runs against :class:`InMemoryRepository` in tests with no database and
no behavioural difference in the branches that matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triage.db.models import DiagnosisRow, EvaluationRow, TicketRow
from triage.schemas.common import Feature
from triage.schemas.diagnosis import Diagnosis
from triage.schemas.ticket import PipelineOutcome

OPEN_TICKET_STATES = ("Proposed by agent", "Validated", "In progress")


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


class TriageRepository(Protocol):
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


class SqlRepository:
    """PostgreSQL-backed implementation."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

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

    tickets: dict[str, TicketRecord] = field(default_factory=dict)
    diagnoses: dict[UUID, Diagnosis] = field(default_factory=dict)
    evaluations: list[EvaluationRecord] = field(default_factory=list)

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
