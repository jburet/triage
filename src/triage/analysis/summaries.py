"""The two F0 summaries, as the cartography graph asks for them (architecture §2.5).

The question text lives here rather than in a node because it is part of the
contract with the entrypoint: the prompt inside the sandbox states the areas, and
this states what the graph wants the summary *for*. A node that built its own
request would drift from the prompt without anything failing.
"""

from __future__ import annotations

from triage.analysis.runner import AnalysisRunner
from triage.schemas.analysis import AnalysisKind, AnalysisRequest, AnalysisResult

REPO_QUESTION = (
    "Summarise this application repository for the system map: what it is written in, "
    "where execution starts, what it exposes, what it calls, how it reaches its data, "
    "and what it already emits for observability."
)

TERRAFORM_QUESTION = (
    "Summarise this Terraform repository from its code alone: the resources it declares "
    "and their sizing, the networking, the databases it manages, and which services each "
    "module provisions for."
)


def repo_summary_request(repo_url: str, commit: str) -> AnalysisRequest:
    return AnalysisRequest(
        kind=AnalysisKind.SUMMARIZE_REPO,
        repo_url=repo_url,
        commit=commit,
        question=REPO_QUESTION,
    )


def terraform_summary_request(repo_url: str, commit: str) -> AnalysisRequest:
    return AnalysisRequest(
        kind=AnalysisKind.SUMMARIZE_TERRAFORM,
        repo_url=repo_url,
        commit=commit,
        question=TERRAFORM_QUESTION,
    )


async def summarize_repo(runner: AnalysisRunner, *, repo_url: str, commit: str) -> AnalysisResult:
    """A ``RepoSummary`` for this repository at this commit, or a stated failure."""
    return await runner.run(repo_summary_request(repo_url, commit))


async def summarize_terraform(
    runner: AnalysisRunner, *, repo_url: str, commit: str
) -> AnalysisResult:
    """A ``TerraformSummary`` for this repository at this commit, or a stated failure."""
    return await runner.run(terraform_summary_request(repo_url, commit))
