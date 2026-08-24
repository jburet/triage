"""The production runner (plan M2 phase 1.3).

The Job has no way to answer over the network, so the result channel is one row
in ``triage.analysis_results`` keyed by Job name (ADR-0009). Everything here is
about what the graph sees when that row does not arrive: a wedged Job, a Job that
errored, a Job that finished without writing. None of them may raise — a failed
analysis is a fact the diagnosis has to record, not an exception that loses the
whole run.
"""

import pytest

from tests.conftest import a_repo_summary, an_analysis_request, some_findings
from triage.analysis.jobs import (
    JOB_TIMEOUT_SECONDS,
    FakeJobApi,
    JobApiError,
    JobStatus,
    job_name,
)
from triage.analysis.runner import KubernetesJobRunner
from triage.config import AnalysisJobConfig
from triage.db.repo import InMemoryRepository
from triage.schemas.analysis import AnalysisKind, AnalysisStatus
from triage.schemas.system_map import RepoSummary

SPEC = AnalysisJobConfig(
    namespace="triage", image="registry.invalid/triage-analysis:1", runtime_class="gvisor"
)


class Clock:
    """Time only moves when the runner sleeps, so a 15-minute timeout costs nothing."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def a_runner(jobs, repo, clock, **overrides):
    return KubernetesJobRunner(
        jobs, repo, SPEC, sleep=clock.sleep, clock=clock, poll_interval=5.0, **overrides
    )


async def test_it_returns_the_row_the_job_wrote_and_deletes_the_job():
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    name = job_name(request)
    repo = InMemoryRepository()
    await repo.save_analysis_result(
        job_name=name,
        kind=AnalysisKind.SUMMARIZE_REPO,
        status=AnalysisStatus.SUCCEEDED,
        result=a_repo_summary().model_dump(mode="json"),
    )
    jobs = FakeJobApi()
    clock = Clock()

    result = await a_runner(jobs, repo, clock).run(request)

    assert result.succeeded
    assert isinstance(result.result, RepoSummary)
    assert jobs.deleted == [name]
    assert jobs.created[0]["metadata"]["name"] == name


async def test_it_waits_for_the_row_to_become_terminal():
    request = an_analysis_request(AnalysisKind.CODE_ANALYSIS)
    name = job_name(request)
    repo = InMemoryRepository()
    await repo.save_analysis_result(
        job_name=name, kind=AnalysisKind.CODE_ANALYSIS, status=AnalysisStatus.RUNNING
    )
    clock = Clock()
    jobs = FakeJobApi(statuses=[JobStatus(active=1)])

    async def finish_after_two_polls(seconds: float) -> None:
        await clock.sleep(seconds)
        if len(clock.slept) == 2:
            await repo.save_analysis_result(
                job_name=name,
                kind=AnalysisKind.CODE_ANALYSIS,
                status=AnalysisStatus.SUCCEEDED,
                result=some_findings().model_dump(mode="json"),
            )

    runner = KubernetesJobRunner(
        jobs, repo, SPEC, sleep=finish_after_two_polls, clock=clock, poll_interval=5.0
    )
    result = await runner.run(request)

    assert result.succeeded
    assert clock.slept == [5.0, 5.0]


async def test_a_wedged_job_is_a_failed_result_not_an_exception():
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    repo = InMemoryRepository()
    clock = Clock()
    jobs = FakeJobApi(statuses=[JobStatus(active=1)])

    result = await a_runner(jobs, repo, clock).run(request)

    assert result.status is AnalysisStatus.FAILED
    assert "960" in (result.error or "")
    assert clock.now > JOB_TIMEOUT_SECONDS
    assert jobs.deleted == [job_name(request)]


async def test_a_job_that_errored_is_a_failed_result_carrying_its_reason():
    request = an_analysis_request(AnalysisKind.IAC_ANALYSIS)
    repo = InMemoryRepository()
    clock = Clock()
    jobs = FakeJobApi(statuses=[JobStatus(failed=1, reason="BackoffLimitExceeded")])

    result = await a_runner(jobs, repo, clock).run(request)

    assert result.status is AnalysisStatus.FAILED
    assert "BackoffLimitExceeded" in (result.error or "")
    assert clock.now == 0.0


async def test_a_job_that_finished_without_writing_a_row_is_a_failed_result():
    """The Job writes the row before it exits, so a succeeded Job with no row never will."""
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    repo = InMemoryRepository()
    clock = Clock()
    jobs = FakeJobApi(statuses=[JobStatus(succeeded=1)])

    result = await a_runner(jobs, repo, clock).run(request)

    assert result.status is AnalysisStatus.FAILED
    assert "no result" in (result.error or "")


async def test_a_row_reporting_a_failure_carries_its_message_through():
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    repo = InMemoryRepository()
    await repo.save_analysis_result(
        job_name=job_name(request),
        kind=AnalysisKind.SUMMARIZE_REPO,
        status=AnalysisStatus.FAILED,
        error="the repository has no Python in it",
    )

    result = await a_runner(FakeJobApi(), repo, Clock()).run(request)

    assert result.status is AnalysisStatus.FAILED
    assert "no Python" in (result.error or "")


async def test_a_row_whose_payload_does_not_validate_is_a_failed_result_naming_the_kind():
    """Plan 1.4, on the production runner."""
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    repo = InMemoryRepository()
    await repo.save_analysis_result(
        job_name=job_name(request),
        kind=AnalysisKind.SUMMARIZE_REPO,
        status=AnalysisStatus.SUCCEEDED,
        result={"repo_url": "github.com/org/payments-api", "languages": []},
    )

    result = await a_runner(FakeJobApi(), repo, Clock()).run(request)

    assert result.status is AnalysisStatus.FAILED
    assert result.result is None
    assert "summarize_repo" in (result.error or "")
    assert "RepoSummary" in (result.error or "")


async def test_a_job_that_cannot_be_submitted_is_a_failed_result():
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    jobs = FakeJobApi(create_error=JobApiError("403 Forbidden: jobs is forbidden"))

    result = await a_runner(jobs, InMemoryRepository(), Clock()).run(request)

    assert result.status is AnalysisStatus.FAILED
    assert "403" in (result.error or "")
    assert jobs.deleted == []


async def test_a_delete_that_fails_does_not_lose_a_good_result():
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    repo = InMemoryRepository()
    await repo.save_analysis_result(
        job_name=job_name(request),
        kind=AnalysisKind.SUMMARIZE_REPO,
        status=AnalysisStatus.SUCCEEDED,
        result=a_repo_summary().model_dump(mode="json"),
    )
    jobs = FakeJobApi(delete_error=JobApiError("404 Not Found"))

    result = await a_runner(jobs, repo, Clock()).run(request)

    assert result.succeeded


@pytest.mark.parametrize("kind", list(AnalysisKind))
async def test_the_job_name_is_a_valid_dns_label_unique_per_request(kind):
    first = job_name(an_analysis_request(kind))
    second = job_name(an_analysis_request(kind))
    assert first != second
    assert first.replace("-", "").isalnum()
    assert first.islower()
    assert len(first) <= 63


async def test_the_manifest_names_the_sandbox_and_carries_the_request():
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    jobs = FakeJobApi(statuses=[JobStatus(failed=1)])
    await a_runner(jobs, InMemoryRepository(), Clock()).run(request)

    spec = jobs.created[0]["spec"]
    pod = spec["template"]["spec"]
    assert pod["runtimeClassName"] == "gvisor"
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 900
    env = {item["name"]: item["value"] for item in pod["containers"][0]["env"]}
    assert env["TRIAGE_ANALYSIS_JOB_NAME"] == job_name(request)
    assert request.commit in env["TRIAGE_ANALYSIS_REQUEST"]


async def test_the_manifest_gives_the_sandbox_the_limits_it_may_not_exceed():
    """A memory limit the Job can be killed for has to be in the manifest to exist."""
    request = an_analysis_request(AnalysisKind.CODE_ANALYSIS)
    jobs = FakeJobApi(statuses=[JobStatus(failed=1)])
    await a_runner(jobs, InMemoryRepository(), Clock()).run(request)

    resources = jobs.created[0]["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["limits"]["memory"] == SPEC.resources.limits["memory"]
    assert resources["limits"]["cpu"] == SPEC.resources.limits["cpu"]
    assert resources["requests"]["memory"] == SPEC.resources.requests["memory"]


async def test_the_wait_outlasts_the_job_deadline_so_kubernetes_reports_it_first():
    """Give up at the Job's own deadline and every deadline failure reads as a hang."""
    request = an_analysis_request(AnalysisKind.CODE_ANALYSIS)
    repo = InMemoryRepository()
    clock = Clock()
    polls = int(JOB_TIMEOUT_SECONDS // 5.0) + 1
    jobs = FakeJobApi(
        statuses=[
            *([JobStatus(active=1)] * polls),
            JobStatus(
                failed=1,
                reason="DeadlineExceeded",
                message="Job was active longer than specified deadline",
            ),
        ]
    )

    result = await KubernetesJobRunner(
        jobs, repo, SPEC, sleep=clock.sleep, clock=clock, poll_interval=5.0
    ).run(request)

    assert clock.now > JOB_TIMEOUT_SECONDS
    assert "DeadlineExceeded" in (result.error or "")
    assert "longer than specified deadline" in (result.error or "")


async def test_a_job_killed_for_its_memory_says_which_limits_it_was_given():
    """`BackoffLimitExceeded` alone does not tell a reader what ceiling was hit."""
    request = an_analysis_request(AnalysisKind.CODE_ANALYSIS)
    jobs = FakeJobApi(
        statuses=[
            JobStatus(
                failed=1,
                reason="BackoffLimitExceeded",
                message="Job has reached the specified backoff limit",
            )
        ]
    )

    result = await a_runner(jobs, InMemoryRepository(), Clock()).run(request)

    assert result.status is AnalysisStatus.FAILED
    error = result.error or ""
    assert "BackoffLimitExceeded" in error
    assert f"memory={SPEC.resources.limits['memory']}" in error
    assert f"deadline={int(JOB_TIMEOUT_SECONDS)}s" in error
