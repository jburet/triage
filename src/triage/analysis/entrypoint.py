"""What runs inside the analysis sandbox (ADR-0009, ADR-0014).

This is the far side of the Job boundary: the clone is the working directory, the
request arrives as an environment variable, and the only way back is stdout for
the local runner and one row in ``triage.analysis_results`` for the Kubernetes
one. Nothing here may raise past ``main`` — a Job that dies with a traceback
leaves the graph polling a row that will never be written, so every failure is
turned into a stated :class:`~triage.schemas.analysis.AnalysisResult` instead.

Four of the five kinds are implemented. ``diff_analysis`` is not: it needs the
patch between two commits rather than one tree, which is a different gather, and
asking for one here is a failure that names the kind rather than an empty answer.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog
from pydantic import ValidationError

from triage.analysis.context import (
    APPLICATION,
    DEFAULT_BUDGET,
    TERRAFORM,
    ContextBudget,
    SelectionProfile,
    gather,
)
from triage.analysis.jobs import JOB_NAME_ENV, REQUEST_ENV
from triage.llm import StructuredLLM, StructuredOutputError
from triage.prompts import render
from triage.schemas.analysis import (
    AnalysisFindings,
    AnalysisKind,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStatus,
)
from triage.schemas.system_map import RepoSummary, TerraformSummary

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Analyser:
    """Which prompt reads which files, and what shape the answer must take."""

    prompt: str
    profile: SelectionProfile
    schema: type[RepoSummary] | type[TerraformSummary] | type[AnalysisFindings]


ANALYSERS: dict[AnalysisKind, Analyser] = {
    AnalysisKind.SUMMARIZE_REPO: Analyser("summarize_repo", APPLICATION, RepoSummary),
    AnalysisKind.SUMMARIZE_TERRAFORM: Analyser("summarize_terraform", TERRAFORM, TerraformSummary),
    # The investigative kinds share one prompt: the question already carries the
    # angle — the graph writes a different one per cause type — and two prompts
    # saying "answer the question from the tree" would drift apart for no reason.
    # What differs is which files are worth opening.
    AnalysisKind.CODE_ANALYSIS: Analyser("investigate", APPLICATION, AnalysisFindings),
    AnalysisKind.IAC_ANALYSIS: Analyser("investigate", TERRAFORM, AnalysisFindings),
}


async def analyse(
    request: AnalysisRequest,
    root: Path,
    llm: StructuredLLM,
    *,
    budget: ContextBudget = DEFAULT_BUDGET,
) -> AnalysisResult:
    """Answer the request against the tree at ``root``, or say why it could not be."""
    analyser = ANALYSERS.get(request.kind)
    if analyser is None:
        return AnalysisResult.failed(
            request.kind,
            f"{request.kind.value} has no entrypoint yet; this image answers "
            f"{', '.join(kind.value for kind in ANALYSERS)}",
        )

    context = gather(root, analyser.profile, budget)
    sections: dict[str, object] = {
        "request": request.model_dump(mode="json", exclude={"request_id"}),
        "repository": context.as_payload(),
    }

    # Twice, at most. A tree of any size is a long answer with a dozen required
    # areas, and the failure seen against a real 50-module repository was two
    # fields simply left out — a mistake the model corrects when shown it, and
    # one that otherwise costs the whole summary and every incident that needed it.
    failure = ""
    for attempt in (1, 2):
        try:
            payload = await llm.call(
                "analysis", render(analyser.prompt, **sections), analyser.schema
            )
        except (StructuredOutputError, ValidationError) as exc:
            failure = str(exc)
            log.warning(
                "analysis_rejected", kind=request.kind.value, attempt=attempt, error=failure
            )
            sections["correction"] = (
                f"Your previous answer did not satisfy the schema and was discarded. "
                f"Answer again, complete this time, filling every required field — with "
                f"an Unknown and a reason where the tree does not say. The validator "
                f"reported:\n{failure}"
            )
            continue
        return AnalysisResult(kind=request.kind, status=AnalysisStatus.SUCCEEDED, result=payload)

    return AnalysisResult.failed(request.kind, f"{request.kind.value} was not answered: {failure}")


def report(result: AnalysisResult) -> int:
    """Hand the result to the local runner: the payload on stdout, or a reason and a code."""
    if result.succeeded and result.result is not None:
        sys.stdout.write(result.result.model_dump_json())
        return 0
    sys.stderr.write(result.error or "the analysis failed without saying why")
    return 1


async def _persist(job: str, result: AnalysisResult) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from triage.config import get_settings
    from triage.db.repo import SqlRepository

    engine = create_async_engine(get_settings().database_url)
    repo = SqlRepository(async_sessionmaker(engine, expire_on_commit=False))
    await repo.save_analysis_result(
        job_name=job,
        kind=result.kind,
        status=result.status,
        result=result.result.model_dump(mode="json") if result.result else None,
        error=result.error,
    )
    await engine.dispose()


async def main() -> int:
    from triage.config import get_settings

    # stdout is the result channel — the local runner parses it as the payload —
    # so every log line has to go the other way. One info line from building the
    # model client was enough to make a good summary unreadable.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

    request = AnalysisRequest.model_validate_json(os.environ[REQUEST_ENV])
    settings = get_settings()
    try:
        # build_llm, not a LiteLLMClient: the sandbox must reach a model the same
        # way the graph does, or the provider that works outside it fails here.
        from triage.runtime import build_llm

        result = await analyse(request, Path.cwd(), build_llm(settings))
    except Exception as exc:
        result = AnalysisResult.failed(request.kind, f"{type(exc).__name__}: {exc}")

    job = os.environ.get(JOB_NAME_ENV)
    if job:
        await _persist(job, result)
    return report(result)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
