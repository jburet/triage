"""What an analysis is allowed to show the model, and what it must admit it never saw.

A repository does not fit in a context window, so the entrypoint sends a bounded
selection: the file tree, then the files that decide the answer — the ones the
caller's mapping named first, then the profile's globs — taken in priority order
until a byte budget is spent. The budget is the point of the
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
from collections.abc import Iterable, Sequence
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


DEFAULT_BUDGET = ContextBudget()


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

DECISIVE_CONFIGURATION = (
    "application.conf",
    "reference.conf",
)
"""The two filenames that, on the JVM, hold the timeouts and the pool sizes."""

RUNTIME_CONFIGURATION = (
    "*.conf",
    "application.yml",
    "application.yaml",
    "application*.properties",
    "logback.xml",
    "logback*.xml",
    "log4j2.xml",
    "*.env.example",
)
"""What the running service is configured by, ahead of what it is written in.

An incident is usually explained by a timeout, a pool size, a heap or a limit,
and on the JVM none of those are in the code."""

NAMED_SOURCE = (
    "Main.scala",
    "*Main.scala",
    "*App.scala",
    "Boot.scala",
    "*Server.scala",
    "*Module.scala",
    "*Routes.scala",
    "*Route.scala",
    "*Controller.scala",
    "*Service.scala",
    "*Repository.scala",
    "*Dao.scala",
    "*Actor.scala",
    "*Supervisor.scala",
    "*Config.scala",
    "*Application.kt",
    "*Controller.kt",
    "*Service.kt",
    "*Application.java",
    "*Controller.java",
    "*Service.java",
    "*Handler.go",
    "*_handler.py",
)
"""Source whose *name* says what it does. Read before source that only says what
language it is."""

ANY_SOURCE = (
    "*.scala",
    "*.java",
    "*.kt",
    "*.py",
    "*.ts",
    "*.tsx",
    "*.js",
    "*.go",
    "*.rs",
    "*.rb",
    "*.cs",
    "*.php",
    "*.ex",
    "*.sql",
)
"""Last, and deliberately blunt: a question about a commit answered without
opening any of its code is answered about nothing."""

SOURCE_SUFFIXES = frozenset(pattern.removeprefix("*") for pattern in ANY_SOURCE)

INVESTIGATION = SelectionProfile(
    name="investigation",
    patterns=(
        *DECISIVE_CONFIGURATION,
        *NAMED_SOURCE,
        *RUNTIME_CONFIGURATION,
        *APPLICATION.patterns,
        *ANY_SOURCE,
    ),
)
"""The investigative kinds' selection, and not the summary's (ADR-0014).

`code_analysis` of the 4261-file Scala repository behind the 2026-08-24 incident
selected 47 files and **not one line of Scala**: twenty-six `build.sbt` files,
the READMEs, the workflows, the chart. The model said so — its findings carried
`not_examined` paths — and returned `low`, which read like judgement about the
code rather than an empty selection.

Order matters as much as coverage. Against the real `platform` tree the
application profile reads 49 files and no code at all; this one reads 60, of
which 38 are Scala — but only once the source whose *name* says what it does
comes ahead of the broad configuration tail. Config first and source last read
the same 60 files as 37 `.conf` and 23 sources, which is a repository described
by its settings.

`APPLICATION` is a list of entry-point *names* drawn from the Python, Node and Go
conventions, and a JVM repository has none of them. Widening it was the wrong
fix: `reads()` is also ADR-0015's invalidation rule, so teaching the summary to
read all source would re-summarise every repository on every commit that touched
a file. A summary describes a repository, an investigation answers a question
about one commit of it, and ADR-0014 leaves the investigative kinds free to
choose differently. This is them choosing.
"""

# An infrastructure question is answered from infrastructure files wherever they
# live, not from files with a `.tf` suffix: on 2026-08-23 three analyses of a
# real incident answered Unknown because the probe timeouts and memory limits
# that explained it are in `helm/zeenea-platform/values.yaml` (M6 3.3).
TERRAFORM = SelectionProfile(
    name="terraform",
    patterns=(
        "values*.yaml",
        "values*.yml",
        "Chart.yaml",
        "*.tf",
        "*.tf.json",
        "*.tfvars",
        "*.hcl",
        "templates/*.yaml",
        "templates/*.yml",
        "helm/*/*.yaml",
        "chart/*/*.yaml",
        "charts/*/*.yaml",
        "k8s/*.yaml",
        "k8s/*/*.yaml",
        "kubernetes/*.yaml",
        "kubernetes/*/*.yaml",
        "deploy/*.yaml",
        "deploy/*/*.yaml",
        "README*",
    ),
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


def reads(path: str, profile: SelectionProfile) -> bool:
    """Whether the gather would read this path.

    The invalidation rule (ADR-0015) asks this of every file a merge touched, so
    that "can this change the summary" has exactly one definition and cannot
    drift from what the gather actually opens.
    """
    relative = PurePosixPath(path)
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts[:-1]):
        return False
    if _matches(relative, NEVER_READ):
        return False
    return _matches(relative, profile.patterns)


def in_profile_order(paths: Iterable[str], profile: SelectionProfile) -> list[str]:
    """The paths this profile reads, ordered as the gather would open them."""
    selected = [PurePosixPath(path) for path in paths if reads(path, profile)]
    return [path.as_posix() for path in _in_priority_order(selected, profile)]


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


MAX_NAMED_MATCHES = 3
"""How many trees paths one named path may resolve to before the rest are dropped.

A package path several modules carry is read in all of them, because picking one
would be a guess; a name so common that a dozen match is a selection, not a
location, and spending the whole budget on it loses the answer."""


def _ending_with(named: PurePosixPath, everything: list[PurePosixPath]) -> list[PurePosixPath]:
    """Tree paths this one is a tail of — the module a package path lives under.

    F2 derives ``com/zeenea/repository/OdbClient.scala`` from a fully-qualified
    class name and cannot know whether the build puts it under ``core/src/main/
    scala`` or nowhere (M8 4.1). The suffix is what it does know.
    """
    parts = named.parts
    return [
        path
        for path in everything
        if len(path.parts) > len(parts) and path.parts[-len(parts) :] == parts
    ][:MAX_NAMED_MATCHES]


def _named_first(
    everything: list[PurePosixPath], first: Sequence[str], profile: SelectionProfile
) -> tuple[list[PurePosixPath], list[PurePosixPath]]:
    """The caller's own paths ahead of the profile's, and the ones the tree lacks.

    The mapping knows which chart defines this workload and a glob does not, so
    a budget spent on the repository's other modules is the answer lost (M6 3.2).
    A named path the tree does not carry outright is looked for as a suffix
    before it is called missing.
    """
    here = set(everything)
    present: list[PurePosixPath] = []
    missing: list[PurePosixPath] = []
    for path in dict.fromkeys(PurePosixPath(name) for name in first):
        found = [path] if path in here else _ending_with(path, everything)
        if found:
            present.extend(item for item in found if item not in present)
        else:
            missing.append(path)
    rest = [path for path in _in_priority_order(everything, profile) if path not in set(present)]
    return present + rest, missing


def gather(
    root: Path,
    profile: SelectionProfile,
    budget: ContextBudget = DEFAULT_BUDGET,
    *,
    first: Sequence[str] = (),
) -> RepoContext:
    """Read what the caller named and then what the profile asks for, up to the budget."""
    everything = _walk(root)
    listable = sorted(everything, key=lambda path: (len(path.parts), path.as_posix()))
    tree = listable[: budget.max_tree_entries]

    candidates, missing = _named_first(everything, first, profile)
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

    if missing:
        notes.append(
            f"the mapping says these paths define this workload and the tree at this commit "
            f"has none of them: {', '.join(path.as_posix() for path in missing)}."
        )
    if stopped_at is not None:
        notes.append(
            f"{len(candidates) - stopped_at} further files matched the {profile.name} "
            f"selection but were not read: the budget of {budget.max_files} files and "
            f"{budget.max_total_bytes} bytes was spent."
        )
    if binary:
        notes.append(f"{binary} selected files were not text and were skipped.")
    if files and not any(PurePosixPath(item.path).suffix in SOURCE_SUFFIXES for item in files):
        notes.append(
            f"the {profile.name} selection opened no source file in this repository: every "
            f"file read is a manifest, a document or configuration. Any statement about what "
            f"the code does is unsupported."
        )
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
