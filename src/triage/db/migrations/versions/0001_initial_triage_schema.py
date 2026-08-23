"""Initial Triage schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-23
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "triage"


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
    """Every table carries the same created_at / updated_at pair."""
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_signals"),
        schema=SCHEMA,
    )
    op.create_index("ix_triage_signals_external_id", "signals", ["external_id"], schema=SCHEMA)
    op.create_index("ix_triage_signals_service", "signals", ["service"], schema=SCHEMA)

    op.create_table(
        "diagnoses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(length=8), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("team", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["signal_id"], [f"{SCHEMA}.signals.id"], name="fk_diagnoses_signal_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_diagnoses"),
        schema=SCHEMA,
    )
    op.create_index("ix_triage_diagnoses_service", "diagnoses", ["service"], schema=SCHEMA)

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("jira_key", sa.String(length=32), nullable=False),
        sa.Column("jira_url", sa.Text(), nullable=False),
        sa.Column("project", sa.String(length=32), nullable=False),
        sa.Column("team", sa.String(length=128), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("diagnosis_id", sa.Uuid(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("last_alerted_occurrence", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["diagnosis_id"], [f"{SCHEMA}.diagnoses.id"], name="fk_tickets_diagnosis_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tickets"),
        sa.UniqueConstraint("jira_key", name="uq_tickets_jira_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_triage_tickets_service", "tickets", ["service"], schema=SCHEMA)
    op.create_index("ix_tickets_service_state", "tickets", ["service", "state"], schema=SCHEMA)

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("diagnosis_id", sa.Uuid(), nullable=True),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(length=8), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("compose_attempts", sa.Integer(), nullable=False),
        sa.Column("time_to_ticket_seconds", sa.Float(), nullable=True),
        sa.Column("validated", sa.Boolean(), nullable=True),
        sa.Column("reviewer_feedback_validation", sa.Text(), nullable=True),
        sa.Column("reviewer_feedback_closure", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["diagnosis_id"], [f"{SCHEMA}.diagnoses.id"], name="fk_evaluations_diagnosis_id"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], [f"{SCHEMA}.tickets.id"], name="fk_evaluations_ticket_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluations"),
        schema=SCHEMA,
    )
    op.create_index("ix_triage_evaluations_outcome", "evaluations", ["outcome"], schema=SCHEMA)

    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_name", sa.String(length=253), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_results"),
        sa.UniqueConstraint("job_name", name="uq_analysis_results_job_name"),
        schema=SCHEMA,
    )

    op.create_table(
        "system_map",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("team", sa.String(length=128), nullable=True),
        sa.Column("source_commit", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_system_map"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_system_map_kind_name", "system_map", ["kind", "name"], unique=True, schema=SCHEMA
    )


def downgrade() -> None:
    for table in (
        "system_map",
        "analysis_results",
        "evaluations",
        "tickets",
        "diagnoses",
        "signals",
    ):
        op.drop_table(table, schema=SCHEMA)
