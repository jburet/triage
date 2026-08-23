"""The graph → analysis contract, from the graph's side (plan M2 phase 1.1, 1.4).

A node never runs an analysis itself; it submits a request through ``Deps`` and
gets a validated result back. These tests pin that seam, because everything M2
and M3 build on it assumes the payload it receives has already been checked.
"""

import pytest

from tests.conftest import (
    a_repo_summary,
    an_analysis_request,
    an_analysis_result,
    build_deps,
    run_config,
)
from triage.analysis.runner import FakeAnalysisRunner, dry_run_result
from triage.runtime import deps_from_runnable_config
from triage.schemas.analysis import (
    AnalysisKind,
    AnalysisResult,
    AnalysisStatus,
    payload_schema,
)
from triage.schemas.system_map import RepoSummary, TerraformSummary


async def summarise(_state: object, config: object) -> AnalysisResult:
    """The smallest node that uses the contract: reach deps, submit, return."""
    deps = deps_from_runnable_config(config)  # type: ignore[arg-type]
    return await deps.runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))


async def test_a_node_submits_a_request_and_gets_a_validated_result(config):
    runner = FakeAnalysisRunner(
        results={AnalysisKind.SUMMARIZE_REPO: an_analysis_result(AnalysisKind.SUMMARIZE_REPO)}
    )
    deps = build_deps(config, runner=runner)

    result = await summarise({}, run_config(deps))

    assert result.succeeded
    assert isinstance(result.result, RepoSummary)
    assert [r.kind for r in runner.requests] == [AnalysisKind.SUMMARIZE_REPO]


@pytest.mark.parametrize("kind", list(AnalysisKind))
async def test_the_fake_answers_every_kind_with_its_own_payload_type(kind):
    runner = FakeAnalysisRunner(results={kind: an_analysis_result(kind)})
    result = await runner.run(an_analysis_request(kind))
    assert isinstance(result.result, payload_schema(kind))


async def test_the_fake_walks_a_sequence_and_repeats_its_last_answer():
    """Same semantics as FakeLLM, so a test that ignores call counts writes one element."""
    kind = AnalysisKind.CODE_ANALYSIS
    runner = FakeAnalysisRunner(
        results={kind: [AnalysisResult.failed(kind, "clone failed"), an_analysis_result(kind)]}
    )
    assert not (await runner.run(an_analysis_request(kind))).succeeded
    assert (await runner.run(an_analysis_request(kind))).succeeded
    assert (await runner.run(an_analysis_request(kind))).succeeded


async def test_the_fake_refuses_a_kind_it_was_not_given():
    runner = FakeAnalysisRunner(results={})
    with pytest.raises(AssertionError, match="summarize_repo"):
        await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))


async def test_a_default_answers_the_kinds_that_were_not_canned():
    """Dry run submits no Job and says so, rather than inventing a summary."""
    runner = FakeAnalysisRunner(default=dry_run_result)
    result = await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_TERRAFORM))
    assert result.status is AnalysisStatus.FAILED
    assert "dry run" in (result.error or "")


async def test_diff_analysis_is_the_only_kind_that_takes_two_commits():
    with pytest.raises(ValueError, match="base_commit"):
        an_analysis_request(AnalysisKind.DIFF_ANALYSIS, base_commit=None)
    with pytest.raises(ValueError, match="base_commit"):
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, base_commit="1111111")

    assert an_analysis_request(AnalysisKind.DIFF_ANALYSIS).commits == ("9f2c1ab", "1111111")
    assert an_analysis_request(AnalysisKind.CODE_ANALYSIS).commits == ("9f2c1ab",)


async def test_a_payload_for_the_wrong_kind_is_a_failed_result_naming_the_kind():
    """Plan 1.4: never a partial success — the map would believe it."""
    result = AnalysisResult.from_payload(
        AnalysisKind.SUMMARIZE_TERRAFORM, a_repo_summary().model_dump(mode="json")
    )
    assert result.status is AnalysisStatus.FAILED
    assert result.result is None
    assert "summarize_terraform" in (result.error or "")
    assert "TerraformSummary" in (result.error or "")


async def test_a_result_cannot_claim_success_with_a_payload_of_another_kind():
    with pytest.raises(ValueError, match="summarize_repo"):
        AnalysisResult(
            kind=AnalysisKind.SUMMARIZE_REPO,
            status=AnalysisStatus.SUCCEEDED,
            result=TerraformSummary.model_validate(
                an_analysis_result(AnalysisKind.SUMMARIZE_TERRAFORM).result.model_dump()
            ),
        )
