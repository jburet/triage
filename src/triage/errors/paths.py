"""Where in the repository the exception was raised, from what Datadog names (M8 4.1).

Error Tracking's ``file_path`` is not a path. Measured on the org on 2026-08-25,
every one of 202 issues named its source as a **fully-qualified class name** —
``zeenea.repository.orientdb.OdbClient.scala`` — and its function as the JVM
symbol, ``$anonfun$load$6``. Handed straight to ``AnalysisRequest.paths`` neither
matches anything in a tree, which is exactly the failure M7 3.3 measured: a
``code_analysis`` of a 4261-file Scala repository that read 47 files of build
configuration and not one line of Scala.

Two conversions, both of them conventions rather than observations, and both
stated as such. The package becomes a directory, and the source root is the JVM's
``src/main/<language>`` — which is a convention a multi-module build breaks by
nesting it under the module, so the package-relative path is offered as well and
:func:`triage.analysis.context.gather` resolves whichever the tree actually
carries. And a Scala lambda's synthetic name carries the method it was written
inside, which is the name a developer would search for.

Nothing here guesses at a path it cannot derive: a file name with no package
stays a file name, and an issue naming no file at all produces no paths and a
reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCE_ROOTS: dict[str, str] = {
    "scala": "src/main/scala",
    "java": "src/main/java",
    "kt": "src/main/kotlin",
    "kts": "src/main/kotlin",
    "groovy": "src/main/groovy",
}
"""The JVM's layout convention, by file suffix. Nothing else has one worth guessing."""

_QUALIFIED = re.compile(
    r"^(?P<package>(?:[A-Za-z_$][\w$]*\.)+)(?P<name>[A-Za-z_$][\w$]*)\.(?P<suffix>[a-z]+)$"
)

_LAMBDA = re.compile(r"^(?:\$anonfun\$|lambda\$)(?P<method>[^$]+)\$")


@dataclass(frozen=True)
class SourceLocation:
    """Candidate paths for one issue's source, and what has to be said about them.

    ``paths`` is ordered: the most conventional spelling first, because the
    gather reads in order and stops at its budget. ``derived`` is what separates
    a path Datadog gave from one this module built, and ``caveat`` is the
    sentence a report carries when it did the building.
    """

    paths: tuple[str, ...]
    derived: bool
    caveat: str | None = None


def source_location(file_path: str | None, function_name: str | None) -> SourceLocation:
    """The paths an analysis should open first, from the issue's own two fields."""
    if not file_path:
        return SourceLocation(
            paths=(),
            derived=False,
            caveat=(
                "Datadog named no file for this issue, so the analysis was pointed at "
                "nothing in particular and read the repository by its own selection."
            ),
        )
    if "/" in file_path:
        return SourceLocation(paths=(file_path,), derived=False)

    match = _QUALIFIED.match(file_path)
    if match is None:
        return SourceLocation(
            paths=(file_path,),
            derived=True,
            caveat=(
                f"`{file_path}` names no package, so the analysis was pointed at a file of "
                f"that name wherever the tree carries one."
            ),
        )

    package = match.group("package").rstrip(".").replace(".", "/")
    suffix = match.group("suffix")
    relative = f"{package}/{match.group('name')}.{suffix}"
    root = SOURCE_ROOTS.get(suffix)
    paths = (f"{root}/{relative}", relative) if root else (relative,)
    function = enclosing_function(function_name)
    return SourceLocation(
        paths=paths,
        derived=True,
        caveat=(
            f"`{file_path}` is a fully-qualified class name, not a path in the repository. "
            f"It was read as the package `{match.group('package').rstrip('.')}`"
            + (f" and the method `{function}`" if function else "")
            + f", and the analysis was pointed at `{paths[0]}` and at the package-relative "
            f"path wherever the tree carries it. Which module holds it was not observed."
        ),
    )


def enclosing_function(function_name: str | None) -> str | None:
    """The method a synthetic lambda symbol was written inside, or the name itself.

    ``$anonfun$load$6`` is scalac's name for the sixth anonymous function in
    ``load``; ``lambda$load$3`` is javac's. Neither appears in the source, so a
    report that quoted them would send a developer searching for a string that is
    not in the file.
    """
    if not function_name:
        return None
    match = _LAMBDA.match(function_name)
    return match.group("method") if match else function_name
