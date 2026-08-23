"""The architecture document's repository map, parsed.

Three facts in it are load-bearing and are in no cluster: that ``platform`` is
the only mono-tenant workload, that ``platform-infra`` provisions it per tenant,
and that everything else is deployed by the shared ``application-deployer``. The
document is someone's Markdown, dated and hand-written, so the parser refuses
what it does not recognise instead of filing it under the nearest value: a
tenancy this enum has not been taught is a repository named in an error, which a
person then resolves, and never a row that silently claims to be multi-tenant.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from triage.schemas.system_map import Deployer, SeedEntry, Tenancy

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCUMENT_PATH = REPO_ROOT / "docs" / "reference-aws-architecture-2026-04-20.md"
SEED_PATH = REPO_ROOT / "config" / "repository-map.yaml"

HEADING = "### 1.1 Repository Map"

COLUMNS = ("Repository", "Role", "Tech Stack", "Tenancy Model", "Deployment Method")
"""The table's own header. A different one is a document that was restructured."""

_TENANCY: tuple[tuple[str, Tenancy], ...] = (
    ("mono-tenant", Tenancy.MONO_TENANT),
    ("multi-tenant", Tenancy.MULTI_TENANT),
    ("per-tenant provisioning", Tenancy.PER_TENANT_PROVISIONING),
    ("n/a", Tenancy.NOT_APPLICABLE),
)

_DEPLOYER: tuple[tuple[str, Deployer], ...] = (
    ("platform-infra", Deployer.PLATFORM_INFRA),
    ("application-deployer", Deployer.APPLICATION_DEPLOYER),
    ("github actions", Deployer.GITHUB_ACTIONS),
    ("aws org", Deployer.AWS_ORGANIZATION),
    ("referenced by", Deployer.NOT_DEPLOYED),
)

_MARKUP = re.compile(r"[`*]")


class SeedParseError(ValueError):
    """The document no longer says what the parser reads. Never a partial import."""


def _clean(cell: str) -> str:
    return _MARKUP.sub("", cell).replace("\\", "").strip()


def _row_cells(line: str) -> list[str]:
    return [_clean(cell) for cell in line.strip().strip("|").split("|")]


def _table(text: str) -> list[list[str]]:
    """The rows of the repository-map table, header and separator removed."""
    if HEADING not in text:
        raise SeedParseError(f"the document has no {HEADING!r} section")
    lines = text.split(HEADING, 1)[1].splitlines()
    rows: list[list[str]] = []
    for line in lines:
        if line.startswith("|"):
            rows.append(_row_cells(line))
        elif rows:
            break  # the table ends where the pipes do; the rest of the document is not it
    if len(rows) < 3:
        raise SeedParseError(f"{HEADING!r} carries no table")
    header, separator, *body = rows
    if tuple(header) != COLUMNS:
        raise SeedParseError(
            f"the repository map's columns are {header}, not {list(COLUMNS)} — the document "
            f"was restructured and this parser reads the wrong cells"
        )
    if not all(set(cell) <= set("-: ") for cell in separator):
        raise SeedParseError(f"expected a header separator under {COLUMNS}, got {separator}")
    return body


def _match[T](cell: str, table: tuple[tuple[str, T], ...]) -> T | None:
    lowered = cell.lower()
    return next((value for marker, value in table if marker in lowered), None)


def parse_document(text: str) -> list[SeedEntry]:
    """Every row of the repository map, or an error naming the rows that failed."""
    entries: list[SeedEntry] = []
    unrecognised: list[str] = []
    for cells in _table(text):
        if len(cells) != len(COLUMNS):
            unrecognised.append(f"{cells[0] if cells else '?'}: {len(cells)} cells, expected 5")
            continue
        repository, role, _stack, tenancy_cell, deployment_cell = cells
        tenancy = _match(tenancy_cell, _TENANCY)
        deployment = _match(deployment_cell, _DEPLOYER)
        if tenancy is None:
            unrecognised.append(f"{repository}: tenancy {tenancy_cell!r}")
        if deployment is None:
            unrecognised.append(f"{repository}: deployment {deployment_cell!r}")
        if tenancy is not None and deployment is not None:
            entries.append(
                SeedEntry(repository=repository, role=role, tenancy=tenancy, deployment=deployment)
            )
    if unrecognised:
        raise SeedParseError(
            "the repository map has cells this parser does not recognise, so it is not "
            "imported at all: " + "; ".join(unrecognised)
        )
    return entries


def parse_file(path: Path = DOCUMENT_PATH) -> list[SeedEntry]:
    return parse_document(path.read_text(encoding="utf-8"))


BANNER = """\
# The seed of the service map: which repositories exist, which of them is
# mono-tenant, and which IaC repository deploys each.
#
# Generated from {source} by
# scripts/generate_repository_map.py. Do not hand-edit: regenerate and review the
# diff, which is the only place the document's drift becomes visible.
"""


def dump_seed(entries: list[SeedEntry], source: Path = DOCUMENT_PATH) -> str:
    """The seed as the versioned YAML file, banner included."""
    body = yaml.safe_dump(
        {
            "source": str(source.relative_to(REPO_ROOT)),
            "repositories": [entry.model_dump(mode="json") for entry in entries],
        },
        sort_keys=False,
        allow_unicode=True,
    )
    return BANNER.format(source=source.relative_to(REPO_ROOT)) + body


def load_seed(path: Path = SEED_PATH) -> list[SeedEntry]:
    """The committed seed. Read from the generated file, never from the document.

    The document is prose that a person edits; this is data that a script writes
    and a reviewer approves, and only the second is safe to load at run time.
    """
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [SeedEntry.model_validate(row) for row in raw["repositories"]]


def seed_for(entries: list[SeedEntry], repository: str) -> SeedEntry | None:
    return next((entry for entry in entries if entry.repository == repository), None)
