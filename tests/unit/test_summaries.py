"""The seam the cartography graph uses (plan M2 phase 2.1, 2.2).

A node asks for a summary of a repository at a commit; what runs it — a fake, a
subprocess, a sandboxed Job — is none of its business.
"""

from tests.conftest import an_analysis_result, canned_runner
from triage.analysis.summaries import summarize_repo, summarize_terraform
from triage.schemas.analysis import AnalysisKind
from triage.schemas.system_map import RepoSummary, TerraformSummary


async def test_summarising_an_application_repo_asks_for_that_kind_at_that_commit():
    runner = canned_runner()

    result = await summarize_repo(runner, repo_url="github.com/org/payments-api", commit="9f2c1ab")

    assert isinstance(result.result, RepoSummary)
    [request] = runner.requests_for(AnalysisKind.SUMMARIZE_REPO)
    assert (request.repo_url, request.commit) == ("github.com/org/payments-api", "9f2c1ab")
    assert request.base_commit is None


async def test_summarising_a_terraform_repo_asks_for_the_terraform_kind():
    runner = canned_runner()

    result = await summarize_terraform(runner, repo_url="github.com/org/infra", commit="4b1e0cd")

    assert isinstance(result.result, TerraformSummary)
    assert [r.kind for r in runner.requests] == [AnalysisKind.SUMMARIZE_TERRAFORM]


async def test_a_failed_summary_comes_back_as_a_result_not_an_exception():
    """The graph decides what a missing summary means; the seam does not raise."""
    kind = AnalysisKind.SUMMARIZE_REPO
    runner = canned_runner()
    runner.results = {
        **runner.results,
        kind: an_analysis_result(kind).model_copy(
            update={"status": "failed", "result": None, "error": "clone failed: no such commit"}
        ),
    }

    result = await summarize_repo(runner, repo_url="github.com/org/x", commit="deadbee")

    assert not result.succeeded
    assert "no such commit" in (result.error or "")
