"""Alert cycles on signals, and the poller watermark.

F1 no longer receives alerts; it polls for them (ADR-0017) and analyses one only
once it has persisted (ADR-0018). Both decisions are visible here: a signal is
now one alert *cycle*, keyed by monitor and firing group, carrying how long it
lasted, and the poller keeps where it got to in a table of its own.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "triage"


def upgrade() -> None:
    op.add_column("signals", sa.Column("team", sa.String(length=128)), schema=SCHEMA)
    op.add_column("signals", sa.Column("monitor_id", sa.BigInteger()), schema=SCHEMA)
    op.add_column("signals", sa.Column("firing_group", sa.Text()), schema=SCHEMA)
    op.add_column("signals", sa.Column("cycle_key", sa.String(length=128)), schema=SCHEMA)
    op.add_column("signals", sa.Column("fired_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("signals", sa.Column("recovered_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("signals", sa.Column("duration_seconds", sa.Float()), schema=SCHEMA)

    op.create_index("ix_signals_cycle_key", "signals", ["cycle_key"], unique=False, schema=SCHEMA)
    op.create_index(
        "ix_signals_monitor_group",
        "signals",
        ["monitor_id", "firing_group"],
        unique=False,
        schema=SCHEMA,
    )
    # The overlapping poll window re-reads events it has already stored; one
    # unique event id is what makes replaying them a no-op rather than a duplicate.
    op.create_unique_constraint("uq_signals_external_id", "signals", ["external_id"], schema=SCHEMA)

    op.create_table(
        "poller_watermarks",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("name", name="pk_poller_watermarks"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("poller_watermarks", schema=SCHEMA)
    op.drop_constraint("uq_signals_external_id", "signals", schema=SCHEMA, type_="unique")
    op.drop_index("ix_signals_monitor_group", table_name="signals", schema=SCHEMA)
    op.drop_index("ix_signals_cycle_key", table_name="signals", schema=SCHEMA)
    for column in (
        "duration_seconds",
        "recovered_at",
        "fired_at",
        "cycle_key",
        "firing_group",
        "monitor_id",
        "team",
    ):
        op.drop_column("signals", column, schema=SCHEMA)
