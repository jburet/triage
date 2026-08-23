"""Whether a merge can have changed a repository's summary (ADR-0006, ADR-0015).

ADR-0006 asks for "re-summarise only the touched areas" and leaves the heuristic
undefined. This is it, and it is deliberately a rule rather than a model call: it
decides whether to spend an ``analysis`` tier call, so a decision that itself
needed one would save nothing.

A path matters exactly when the gather would read it — :func:`context.reads` is
the single definition, so the rule cannot come to disagree with what the
summariser actually opens. Everything else is inert: a changed test, a changed
changelog, a lockfile nobody reads.

What it cannot see, and what the weekly full pass is for:

- A change in one area invalidating a conclusion recorded about another. A moved
  entry point or a renamed dependency reads as one touched file and can make a
  sentence about a different file wrong.
- A purely additive change in an unread area. The file tree travels to the model
  alongside the files, so a new directory can shift a summary even though
  nothing readable changed.
- Anything the previous summary got wrong. A carried-forward summary keeps its
  mistakes; only a re-summarise can correct them.

The areas are recorded but do not narrow the work: ADR-0015 explains why the unit
of invalidation is the whole repository summary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from triage.analysis.context import APPLICATION, TERRAFORM, SelectionProfile, reads
from triage.config import RepoKind

ROOT_AREA = "<root>"

CONTAINER_DIRECTORIES = frozenset({"src", "lib", "app", "apps", "pkg", "cmd", "internal", "main"})
"""Directories that hold the packages rather than being one. An area reaches past them."""

_MAX_NAMED_PATHS = 3


def profile_for(kind: RepoKind) -> SelectionProfile:
    return TERRAFORM if kind is RepoKind.TERRAFORM else APPLICATION


def area_of(path: str, kind: RepoKind) -> str:
    """The part of the repository a changed file belongs to."""
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if len(parts) <= 1:
        return ROOT_AREA
    if parts[0] in CONTAINER_DIRECTORIES or (kind is RepoKind.TERRAFORM and parts[0] == "modules"):
        return "/".join(parts[:2]) if len(parts) > 2 else parts[0]
    return parts[0]


@dataclass(frozen=True)
class Invalidation:
    """What a merge touched, and whether the summary has to be redone because of it."""

    areas: frozenset[str]
    read_paths: tuple[str, ...]
    reason: str

    @property
    def stale(self) -> bool:
        return bool(self.read_paths)


def _reason(paths: Sequence[str], read_paths: tuple[str, ...], areas: frozenset[str]) -> str:
    if not paths:
        return "no files changed between the summarised commit and this one"
    if not read_paths:
        listed = ", ".join(sorted(areas))
        return (
            f"{len(paths)} changed files, none of them read by the summariser (touched: {listed})"
        )
    named = ", ".join(read_paths[:_MAX_NAMED_PATHS])
    more = len(read_paths) - _MAX_NAMED_PATHS
    return f"the summariser reads {named}" + (f" and {more} more" if more > 0 else "")


def invalidation_for(paths: Sequence[str], kind: RepoKind) -> Invalidation:
    """Judge a merge's changed paths against what a summary of this kind is built from."""
    profile = profile_for(kind)
    read_paths = tuple(path for path in paths if reads(path, profile))
    areas = frozenset(area_of(path, kind) for path in paths)
    return Invalidation(
        areas=areas, read_paths=read_paths, reason=_reason(paths, read_paths, areas)
    )
