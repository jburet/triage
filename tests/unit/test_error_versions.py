"""The commit an exception's version names, and the release boundary (M8 4.2, 4.3)."""

from datetime import UTC, datetime, timedelta

from tests.conftest import a_workload, declaring
from triage.db.repo import InMemoryRepository
from triage.errors.versions import commit_for_group, deployment_hypothesis, loudest_service
from triage.integrations.github import FakeGitHubClient, GitHubError
from triage.schemas.errors import CommitChoice, ErrorGroup, ErrorTrack, Novelty
from triage.schemas.hypothesis import CauseType

NOW = datetime(2026, 8, 25, 5, 35, tzinfo=UTC)
REPO = "github.com/org/payments-api"


def a_group(**overrides: object) -> ErrorGroup:
    base: dict[str, object] = {
        "key": "EntityNotFoundException|OdbClient.scala|$anonfun$load$6|payments-api",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "repository": "payments-api",
        "repo_url": REPO,
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"payments-eu": 40, "payments-us": 5869},
        "occurrences": 5909,
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


async def a_repository() -> InMemoryRepository:
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-us",
            repository="payments-api",
            repo_url=REPO,
            deployed_commit="cafe123",
        )
    )
    return repo


def test_the_group_is_resolved_against_the_service_raising_it_most():
    assert loudest_service(a_group()) == "payments-us"


async def test_a_version_a_repository_claims_is_the_commit_the_analysis_reads():
    github = FakeGitHubClient(tags={(REPO, "501"): "deadbee"})
    choice = await commit_for_group(
        github, declaring(REPO), await a_repository(), a_group(first_seen_version="501")
    )

    assert choice.commit == "deadbee"
    assert choice.claimed
    assert "version `501`" in choice.rung
    assert "as it stood when the defect appeared" in choice.rung


async def test_the_declared_tag_spelling_is_the_one_looked_up():
    github = FakeGitHubClient(tags={(REPO, "v501"): "deadbee"})
    config = declaring(REPO, tag_template="v{tag}")

    choice = await commit_for_group(
        github, config, await a_repository(), a_group(first_seen_version="501")
    )

    assert choice.commit == "deadbee"
    assert github.tag_lookups == [(REPO, "v501")]


async def test_nothing_claiming_the_version_falls_back_and_says_it_fell_back():
    github = FakeGitHubClient(tags={})
    choice = await commit_for_group(
        github, declaring(REPO), await a_repository(), a_group(first_seen_version="501")
    )

    assert choice.commit == "cafe123"
    assert not choice.claimed
    assert "no tag `501`" in choice.rung
    assert "not the build the defect entered at" in choice.rung


async def test_the_normal_case_is_no_version_at_all_and_it_reads_as_a_fallback():
    """Measured: blank on 15 of 15 issues in the reference hour (M8 phase 1)."""
    choice = await commit_for_group(
        FakeGitHubClient(), declaring(REPO), await a_repository(), a_group()
    )

    assert choice.commit == "cafe123"
    assert not choice.claimed
    assert choice.version is None
    assert "recorded no version" in choice.rung


async def test_a_github_refusal_is_a_fallback_naming_the_refusal():
    github = FakeGitHubClient(tags={}, error=GitHubError("401: Bad credentials"))

    choice = await commit_for_group(
        github, declaring(REPO), await a_repository(), a_group(first_seen_version="501")
    )

    assert choice.commit == "cafe123"
    assert not choice.claimed
    assert "401: Bad credentials" in choice.rung


def test_a_recorded_version_produces_a_deployment_hypothesis_naming_both():
    hypothesis = deployment_hypothesis(
        a_group(first_seen_version="501", last_seen_version="514"),
        CommitChoice(commit="deadbee", version="501", claimed=True, rung="…"),
    )

    assert hypothesis is not None
    assert hypothesis.cause_type is CauseType.DEPLOYMENT
    assert "`501`" in hypothesis.description
    assert "`514`" in hypothesis.description
    assert hypothesis.commit == "deadbee"
    assert hypothesis.base_commit is None


def test_no_version_produces_no_deployment_hypothesis():
    assert deployment_hypothesis(a_group(), CommitChoice(rung="…")) is None
