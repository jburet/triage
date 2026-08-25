"""One error group into ranked hypotheses, with the code's address attached (M8 4.1-4.3).

F1's ``qualify`` reads a collection and guesses where to look. F2 is *told*: the
issue names the exception type, the file and the function, so the model's job here
is narrower — propose mechanisms — and the two facts that decide what an analysis
actually opens are rules rather than answers.

The first is the address. When the collection retrieved a real stack its frames
name real files and real line numbers, and those are what the analysis opens
(ADR-0029). When it did not, Datadog's ``file_path`` is a fully-qualified class
name and its ``function_name`` a JVM symbol, neither of which matches anything in
a tree, and :mod:`triage.errors.paths` converts them by convention. Either way
they go into the hypothesis ahead of the selection profile's globs, and the
report says which of the two it had. Handing them over raw is what M7 3.3
measured: 47 files of build configuration and not one line of Scala.

The second is the commit. A repository that carries a tag for the version the
exception was *first seen on* answers what the code looked like when the defect
appeared; when nothing claims it the commit falls back to the service map's, and
the two are labelled differently because they are different claims (ADR-0019,
ADR-0020, :mod:`triage.errors.versions`).

The model may not name either. ``ProposedCause`` has no field for a path or a
commit, and nothing here fills one from prose.
"""

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from triage.config import PLATFORM_TEAM
from triage.errors.paths import enclosing_function, source_location
from triage.errors.versions import (
    commit_for_group,
    deployment_hypothesis,
    loudest_service,
)
from triage.graphs.state import CodeExceptionState
from triage.nodes.qualify import qualified
from triage.runtime import deps_from_runnable_config
from triage.schemas.collection import ProposedCause
from triage.schemas.common import Feature
from triage.schemas.errors import CodeExceptionContext, CommitChoice, ErrorGroup
from triage.schemas.hypothesis import CauseType, Hypothesis

log = structlog.get_logger(__name__)

READS_CODE = (CauseType.APP, CauseType.DEPLOYMENT)
"""The cause types whose analysis opens a tree, and so the ones the paths help."""


def exception_payload(group: ErrorGroup) -> dict[str, Any]:
    """The group as the prompt and the report both see it — its own fields, nothing added."""
    return {
        "error_type": group.error_type,
        "message": group.sample_message,
        "file": group.file_path,
        "function": group.function_name,
        "method": enclosing_function(group.function_name),
        "occurrences_this_window": group.occurrences,
        "occurrences_per_service": group.services,
        "occurrences_cumulative": group.cumulative_occurrences,
        "services": sorted(group.services),
        "repository": group.repository,
        "first_seen": group.first_seen,
        "last_seen": group.last_seen,
        "first_seen_version": group.first_seen_version,
        "last_seen_version": group.last_seen_version,
        "novelty": group.novelty.value,
        "track": group.track.value,
        "issue_ids": group.issue_ids,
        "report_number": group.analysis_count or 1,
    }


def _hypothesis(cause: ProposedCause, choice: CommitChoice, paths: tuple[str, ...]) -> Hypothesis:
    """One proposed cause, with the address and the commit the run resolved.

    A cause that reads code with no commit is left as it is rather than downgraded
    the way ``qualify`` downgrades one: F1 downgrades to keep an analysis
    runnable, and for F2 the code *is* the report, so "no commit could be
    resolved" has to be said out loud instead of turned into an infrastructure
    question nobody asked.
    """
    return Hypothesis(
        cause_type=cause.cause_type,
        service=cause.service,
        commit=choice.commit if cause.cause_type in READS_CODE else None,
        description=cause.description,
        rank_score=cause.rank_score,
        paths=list(paths) if cause.cause_type in READS_CODE else [],
    )


async def qualify_exception(
    state: CodeExceptionState, config: RunnableConfig | None = None
) -> CodeExceptionState:
    deps = deps_from_runnable_config(config)
    group = state["group"]
    collection = state["collection"]
    exemplar = collection.exemplar
    located = source_location(
        group.file_path, group.function_name, exemplar.frames if exemplar else ()
    )
    choice = await commit_for_group(deps.github, deps.config, deps.repo, group)

    entry = await deps.repo.system_map_for_service(group.repository or "")
    sections: dict[str, object] = {
        "exception": exception_payload(group),
        "collected": collection.as_payload(),
        "system_map": (
            entry.model_dump(mode="json")
            if entry
            else {
                "repository": group.repository,
                "known": False,
                "note": "F0 has no cartography for this repository: its entry points and "
                "dependencies are unknown.",
            }
        ),
    }
    qualification = await qualified(deps, "qualify_exception", sections)

    boundary = deployment_hypothesis(group, choice)
    proposed = [_hypothesis(cause, choice, located.paths) for cause in qualification.causes]
    hypotheses = [boundary, *proposed] if boundary is not None else proposed
    log.info(
        "exception_qualified",
        group=group.key,
        causes=len(proposed),
        commit=choice.commit,
        from_version=choice.claimed,
        paths=list(located.paths),
        paths_observed=not located.derived and bool(located.frames),
    )
    return {
        "qualification": qualification,
        "hypotheses": hypotheses,
        "feature": Feature.F2,
        "service": loudest_service(group),
        "team": group.team or PLATFORM_TEAM,
        "exception": CodeExceptionContext(
            group=group,
            collection=collection,
            commit=choice,
            source_caveat=located.caveat,
        ),
        "context": {
            "exception": exception_payload(group),
            "telemetry_summary": qualification.summary,
            "collected": collection.as_payload(),
            "source_paths": list(located.paths),
            "source_frames": list(located.frames),
            "source_caveat": located.caveat,
            "commit_read": choice.model_dump(mode="json"),
        },
    }
