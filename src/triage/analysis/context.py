"""What an analysis is allowed to show the model, and what it must admit it never saw.

A repository does not fit in a context window, so the entrypoint sends a bounded
selection: the file tree, then the files that decide the answer, taken in
priority order until a byte budget is spent. The budget is the point of the
module — an unbounded gather dies on the first large repository, and a silent one
produces a summary that looks complete and is not. Everything left out is listed
back to the model, so an area that could not be examined becomes an
:class:`~triage.schemas.common.Unknown` carrying that reason instead of a guess.

Terraform state is excluded wherever it appears: the roadmap's F0 reads
infrastructure *code*, and a summary that quietly mixed in state would describe a
cluster nobody can reproduce from the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".gradle",
        ".idea",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".terraform",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "target",
        "vendor",
        "venv",
    }
)

NEVER_READ = (
    "*.tfstate",
    "*.tfstate.*",
    "*.lock",
    "*-lock.json",
    "go.sum",
    "*.min.js",
    "*.map",
)
"""Files that are either state, machine-generated, or both. Excluded from the tree as well."""

_BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class ContextBudget:
    """The hard limits. Defaults sized for one ``analysis``-tier call per repository."""

    max_tree_entries: int = 600
    max_files: int = 60
    max_total_bytes: int = 300_000
    max_file_bytes: int = 40_000


@dataclass(frozen=True)
class SelectionProfile:
    """Priority-ordered globs. Earlier patterns are read first when the budget runs short.

    Patterns match from the right, so ``models.py`` finds one at any depth and
    ``api/*.py`` finds a package's modules wherever the package lives.
    """

    name: str
    patterns: tuple[str, ...]


APPLICATION = SelectionProfile(
    name="application",
    patterns=(
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements*.txt",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "build.sbt",
        "Gemfile",
        "composer.json",
        "mix.exs",
        "*.csproj",
        "README*",
        "Dockerfile",
        "Dockerfile.*",
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "Makefile",
        "main.py",
        "__main__.py",
        "app.py",
        "asgi.py",
        "wsgi.py",
        "manage.py",
        "main.go",
        "main.rs",
        "index.js",
        "index.ts",
        "server.js",
        "server.ts",
        "*Application.java",
        "Main.java",
        "Main.scala",
        "urls.py",
        "routes.py",
        "router.py",
        "routers/*.py",
        "api.py",
        "api/*.py",
        "routes/*",
        "controllers/*",
        "handlers/*",
        "endpoints/*",
        "models.py",
        "models/*.py",
        "db.py",
        "database.py",
        "alembic.ini",
        "env.py",
        "schema.sql",
        "*.prisma",
        "settings.py",
        "config.py",
        "config.yaml",
        "config.yml",
        "telemetry.py",
        "metrics.py",
        "logging.py",
        "tracing.py",
        "observability.py",
        "worker.py",
        "workers/*.py",
        "tasks.py",
        "consumer.py",
        "consumers/*.py",
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        "values.yaml",
        "deployment.yaml",
        "k8s/*",
        "deploy/*",
    ),
)

TERRAFORM = SelectionProfile(
    name="terraform",
    patterns=("*.tf", "*.tf.json", "*.tfvars", "*.hcl", "README*"),
)


@dataclass(frozen=True)
class SelectedFile:
    path: str
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class RepoContext:
    """One repository as the model sees it: the tree, the files read, the gaps."""

    tree: tuple[str, ...]
    files: tuple[SelectedFile, ...]
    not_examined: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "tree": list(self.tree),
            "files": [
                {"path": item.path, "truncated": item.truncated, "text": item.text}
                for item in self.files
            ],
            "not_examined": list(self.not_examined),
        }


def _matches(path: PurePosixPath, patterns: tuple[str, ...]) -> bool:
    return any(path.match(pattern) for pattern in patterns)


def _walk(root: Path) -> list[PurePosixPath]:
    found: list[PurePosixPath] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRECTORIES)
        here = Path(dirpath)
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink():
                continue
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if _matches(relative, NEVER_READ):
                continue
            found.append(relative)
    return found


def _in_priority_order(
    paths: list[PurePosixPath], profile: SelectionProfile
) -> list[PurePosixPath]:
    ordered: list[PurePosixPath] = []
    seen: set[PurePosixPath] = set()
    for pattern in profile.patterns:
        for path in paths:
            if path not in seen and path.match(pattern):
                seen.add(path)
                ordered.append(path)
    return ordered


def _read_text(path: Path, limit: int) -> tuple[str, bool] | None:
    """The file as text, and whether it was cut short. ``None`` when it is not text."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return None
    if b"\0" in raw[:_BINARY_SNIFF_BYTES]:
        return None
    truncated = len(raw) > limit
    return raw[:limit].decode("utf-8", errors="replace"), truncated


def gather(
    root: Path, profile: SelectionProfile, budget: ContextBudget = ContextBudget()
) -> RepoContext:
    """Read what the profile asks for, up to the budget, and record the rest."""
    everything = _walk(root)
    listable = sorted(everything, key=lambda path: (len(path.parts), path.as_posix()))
    tree = listable[: budget.max_tree_entries]

    candidates = _in_priority_order(everything, profile)
    files: list[SelectedFile] = []
    notes: list[str] = []
    total = 0
    binary = 0
    stopped_at: int | None = None

    for index, relative in enumerate(candidates):
        if len(files) >= budget.max_files or total >= budget.max_total_bytes:
            stopped_at = index
            break
        content = _read_text(root / relative, budget.max_file_bytes)
        if content is None:
            binary += 1
            continue
        text, truncated = content
        files.append(SelectedFile(path=relative.as_posix(), text=text, truncated=truncated))
        total += len(text.encode("utf-8"))
        if truncated:
            notes.append(
                f"{relative}: truncated at {budget.max_file_bytes} bytes; the rest was not read."
            )

    if stopped_at is not None:
        notes.append(
            f"{len(candidates) - stopped_at} further files matched the {profile.name} "
            f"selection but were not read: the budget of {budget.max_files} files and "
            f"{budget.max_total_bytes} bytes was spent."
        )
    if binary:
        notes.append(f"{binary} selected files were not text and were skipped.")
    if len(listable) > len(tree):
        notes.append(
            f"{len(listable) - len(tree)} of {len(listable)} paths are not listed in the "
            f"tree: it is capped at {budget.max_tree_entries} entries, shallowest first."
        )

    return RepoContext(
        tree=tuple(path.as_posix() for path in tree),
        files=tuple(files),
        not_examined=tuple(notes),
    )
