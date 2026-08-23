"""Alembic environment.

Scoped to the ``triage`` schema on purpose. Triage's tables share a database
with the LangGraph Platform's checkpoint, thread and run tables; without
``include_object`` an autogenerate run would see them as untracked and offer to
drop them.
"""

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from triage.config import get_settings
from triage.db.models import SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Alembic runs synchronously; strip the async driver from the app's URL."""
    return (
        get_settings()
        .database_url.replace("+psycopg", "")
        .replace("postgresql://", "postgresql+psycopg://")
    )


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if type_ == "table":
        return bool(obj.schema == SCHEMA)
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or _sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # Before anything else: Alembic keeps its own version table in this schema
        # and creates it before running 0001, which is what creates the schema. On
        # an empty database that is a deadlock — "schema triage does not exist" —
        # and `make dev` could never have worked on a fresh volume.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
