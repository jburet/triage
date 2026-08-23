"""What runs inside the analysis sandbox (ADR-0009, ADR-0014).

This is the far side of the Job boundary: the clone is the working directory, the
request arrives as an environment variable, and the only way back is stdout for
the local runner and one row in ``triage.analysis_results`` for the Kubernetes
one. Nothing here may raise past ``main`` — a Job that dies with a traceback
leaves the graph polling a row that will never be written, so every failure is
turned into a stated :class:`~triage.schemas.analysis.AnalysisResult` instead.

Only the two F0 summarisation kinds are implemented. The investigative kinds are
M3's, and asking for one here is a failure that names the kind rather than an
empty answer.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

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
from triage.schemas.analysis import AnalysisKind, AnalysisRequest, AnalysisResult, AnalysisStatus
from triage.schemas.system_map import RepoSummary, TerraformSummary


@dataclass(frozen=True)
class Summariser:
    prompt: str
    profile: SelectionProfile
    schema: type[RepoSummary] | type[TerraformSummary]


SUMMARISERS: dict[AnalysisKind, Summariser] = {
    AnalysisKind.SUMMARIZE_REPO: Summariser("summarize_repo", APPLICATION, RepoSummary),
    AnalysisKind.SUMMARIZE_TERRAFORM: Summariser(
        "summarize_terraform", TERRAFORM, TerraformSummary
    ),
}


async def analyse(
    request: AnalysisRequest,
    root: Path,
    llm: StructuredLLM,
    *,
    budget: ContextBudget = DEFAULT_BUDGET,
) -> AnalysisResult:
    """Summarise the tree at ``root``, or say why it could not be summarised."""
    summariser = SUMMARISERS.get(request.kind)
    if summariser is None:
        return AnalysisResult.failed(
            request.kind,
            f"{request.kind.value} has no entrypoint yet; this image summarises "
            f"{', '.join(kind.value for kind in SUMMARISERS)}",
        )

    context = gather(root, summariser.profile, budget)
    prompt = render(
        summariser.prompt,
        request=request.model_dump(mode="json", exclude={"request_id"}),
        repository=context.as_payload(),
    )
    try:
        payload = await llm.call("analysis", prompt, summariser.schema)
    except (StructuredOutputError, ValidationError) as exc:
        return AnalysisResult.failed(request.kind, f"{request.kind.value} was not answered: {exc}")
    return AnalysisResult(kind=request.kind, status=AnalysisStatus.SUCCEEDED, result=payload)


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

    request = AnalysisRequest.model_validate_json(os.environ[REQUEST_ENV])
    settings = get_settings()
    try:
        from triage.llm import LiteLLMClient

        result = await analyse(
            request, Path.cwd(), LiteLLMClient(settings.litellm_url, settings.litellm_api_key)
        )
    except Exception as exc:
        result = AnalysisResult.failed(request.kind, f"{type(exc).__name__}: {exc}")

    job = os.environ.get(JOB_NAME_ENV)
    if job:
        await _persist(job, result)
    return report(result)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
