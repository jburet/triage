"""Error groups: one code exception, however many tenants raise it.

Keyed on the grouping rule's own output — exception type, source location and
the repository the mono-tenancy rule resolves — so a tick finds an existing
group by recomputing the key rather than by searching for it (ADR-0026). The
counters are the volume gate's (ADR-0025): the cumulative total no single tick
can see, and where the last analysis left it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "triage"


def upgrade() -> None:
    op.create_table(
        "error_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("error_type", sa.String(length=256), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("function_name", sa.Text(), nullable=True),
        sa.Column("repository", sa.String(length=128), nullable=True),
        sa.Column("team", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("cumulative_occurrences", sa.BigInteger(), nullable=False),
        sa.Column("analysed_at_cumulative", sa.BigInteger(), nullable=False),
        sa.Column("analysis_count", sa.Integer(), nullable=False),
        sa.Column("thread_ts", sa.String(length=64), nullable=True),
        sa.Column("first_report_url", sa.Text(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_analysed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_error_groups"),
        sa.UniqueConstraint("group_key", name="uq_error_groups_group_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_error_groups_repository", "error_groups", ["repository"], unique=False, schema=SCHEMA
    )
    op.create_index(
        "ix_error_groups_status", "error_groups", ["status"], unique=False, schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_error_groups_status", table_name="error_groups", schema=SCHEMA)
    op.drop_index("ix_error_groups_repository", table_name="error_groups", schema=SCHEMA)
    op.drop_table("error_groups", schema=SCHEMA)
