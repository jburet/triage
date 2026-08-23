"""Guard against the migration and the models drifting apart.

Alembic's real comparison needs a live database, which CI does not have. This is
the cheap half of it: every table and column the models declare must appear in
the SQL the migrations render. It catches the common mistake — adding a column
to ``models.py`` and forgetting the migration — without needing Postgres.
"""

import subprocess
import sys

import pytest

from tests.conftest import REPO_ROOT
from triage.db.models import Base


@pytest.fixture(scope="module")
def rendered_sql() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.mark.parametrize("table", [t.name for t in Base.metadata.sorted_tables])
def test_every_model_table_is_created(rendered_sql, table):
    assert f"CREATE TABLE triage.{table}" in rendered_sql


@pytest.mark.parametrize(
    ("table", "column"),
    [(t.name, c.name) for t in Base.metadata.sorted_tables for c in t.columns],
)
def test_every_model_column_is_created(rendered_sql, table, column):
    """Created with the table, or added by a later migration — both count."""
    body = rendered_sql.split(f"CREATE TABLE triage.{table} (", 1)[1].split(");", 1)[0]
    added = f"ALTER TABLE triage.{table} ADD COLUMN {column} " in rendered_sql
    assert f"\n    {column} " in body or added


def test_migrations_are_scoped_to_the_triage_schema(rendered_sql):
    """The Platform's checkpoint tables share this database and are not ours to touch."""
    creates = [line for line in rendered_sql.splitlines() if line.startswith("CREATE TABLE")]
    assert creates
    assert all(line.startswith("CREATE TABLE triage.") for line in creates)
    assert "triage.alembic_version" in rendered_sql
