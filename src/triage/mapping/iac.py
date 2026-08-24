"""Where in an IaC repository a workload is defined (M6 3.1).

Which repository provisions a service is only half a mapping, and on 2026-08-23
the other half cost three analyses their answer: they read `platform-infra`,
which was right, at the files a `*.tf` selection finds, which is not where a
StatefulSet's probe timeouts are. So the paths that define *this* workload are
resolved from the repository's own file listing and travel on the entry, ahead
of any glob.

Two things can name the workload, in this order:

A **declaration** on the IaC repository, when there is one. `platform-infra`
organises by what it provisions on — `terraform/eks_module/eks.tf`, holding
`resource "kubernetes_stateful_set_v1" "platform"` and the probe timeouts the
2026-08-22 incident turned on. The workload's name is the resource label, one
level below any path, and no rule over path segments reaches it (ADR-0021).

Otherwise a **directory**, when it is the repository's name or ends in it —
`zeenea-platform` for `platform`. Not when it merely starts with it:
`platform-api` is a different repository, and reading its chart would answer
confidently about a workload this service is not, which is the guess 2.2
refuses one level up.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatch
from pathlib import PurePosixPath

from triage.analysis.context import TERRAFORM, in_profile_order

MAX_PATHS = 40
"""What is cut here is not lost: the profile's own globs still reach it in the sandbox."""


def _names_the_workload(segment: str, name: str) -> bool:
    return segment == name or segment.endswith(f"-{name}")


def _defines(path: str, names: Sequence[str]) -> bool:
    relative = PurePosixPath(path)
    segments = (*relative.parts[:-1], relative.stem)
    return any(_names_the_workload(segment, name) for name in names for segment in segments)


def workload_paths(
    tree: Sequence[str],
    repository: str,
    service: str | None = None,
    declares: Sequence[str] = (),
) -> list[str]:
    """The files in this listing that define the named workload, most decisive first.

    A declaration replaces the name rule rather than adding to it, and answers
    nothing when it matches nothing. It is stated about *this* repository by
    someone who read it; the name rule is a guess about how repositories are
    usually laid out, and falling back to it would answer a question the
    declaration exists to have taken away.
    """
    if declares:
        defining = [path for path in tree if any(fnmatch(path, pattern) for pattern in declares)]
    else:
        names = [repository, service] if service and service != repository else [repository]
        defining = [path for path in tree if _defines(path, names)]
    return in_profile_order(defining, TERRAFORM)[:MAX_PATHS]
