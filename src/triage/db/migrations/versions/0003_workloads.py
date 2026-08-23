"""Derived workloads: which repository a running service is.

Keyed on the service name the cluster uses, which for the mono-tenant
application is a customer — one row per tenant, none of them a repository name.
The system map cannot hold this: it is keyed on the name a repository says it
deploys as (M6).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "triage"


def upgrade() -> None:
    op.create_table(
        "workloads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("repository", sa.String(length=128), nullable=False),
        sa.Column("repo_url", sa.String(length=256), nullable=True),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column("image_digest", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workloads"),
        sa.UniqueConstraint("service", name="uq_workloads_service"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workloads_repository", "workloads", ["repository"], unique=False, schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_workloads_repository", table_name="workloads", schema=SCHEMA)
    op.drop_table("workloads", schema=SCHEMA)
