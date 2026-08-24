"""Where an analysis is told to read, and which of the three answers said so.

The order is the whole point of M6: the derived workload is keyed on the name the
cluster uses, so it is the only one that can answer for a customer's instance of
the mono-tenant platform.
"""

import pytest

from tests.conftest import TENANT, a_service_entry, a_workload, declaring, map_row, mapped
from triage.config import RepoKind
from triage.db.repo import InMemoryRepository
from triage.schemas import SystemMapKind
from triage.schemas.system_map import CommitSource
from triage.scope import Deployment, deployed_repo

PLATFORM = "github.com/zeenea/platform"


@pytest.fixture
def config():
    return declaring(PLATFORM, serves=("plt-*",))


async def with_workload(entry, repo=None):
    repo = repo or InMemoryRepository()
    await repo.upsert_workload(entry)
    return repo


async def test_a_derived_workload_answers_for_a_service_no_repository_claims(config):
    """`plt-hcl-software-uat` is in no system-map entry and never will be."""
    repo = await with_workload(a_workload())

    assert await deployed_repo(config, repo, TENANT) == Deployment(PLATFORM, None)


async def test_a_workload_that_knows_its_commit_gives_that_commit(config):
    repo = await with_workload(a_workload(deployed_commit="9f2c1ab"))

    assert await deployed_repo(config, repo, TENANT) == Deployment(PLATFORM, "9f2c1ab")


async def test_a_workload_whose_commit_is_unknown_falls_back_to_the_summarised_one(config):
    """The old answer, unchanged: a fact about the repository, not about this tenant."""
    repo = await with_workload(a_workload())
    await repo.upsert_system_map_entries(
        [map_row(a_service_entry(name="platform", repo_url=PLATFORM), SystemMapKind.SERVICE)]
    )

    assert await deployed_repo(config, repo, TENANT) == Deployment(PLATFORM, "9f2c1ab")


async def test_the_workload_is_preferred_over_the_map_and_the_patterns(config):
    repo = mapped(a_service_entry(name=TENANT, repo_url="github.com/org/wrong"))
    await repo.upsert_workload(a_workload())

    assert (await deployed_repo(config, repo, TENANT)).repo_url == PLATFORM


async def test_without_a_workload_the_map_still_answers(config):
    repo = mapped(a_service_entry(name="payments-api"))

    assert await deployed_repo(config, repo, "payments-api") == Deployment(
        "github.com/org/payments-api",
        "9f2c1ab",
    )


async def test_without_a_workload_or_a_map_entry_the_patterns_still_answer(config):
    assert await deployed_repo(config, InMemoryRepository(), "plt-merck-qa") == Deployment(
        PLATFORM, None
    )


async def test_a_terraform_question_is_never_answered_by_a_workload(config):
    """Workloads are application repositories; the Terraform kind has its own patterns."""
    repo = await with_workload(a_workload())

    assert await deployed_repo(config, repo, TENANT, RepoKind.TERRAFORM) == Deployment(None, None)


async def test_the_answer_carries_where_the_commit_came_from(config):
    """A commit read off the image and one read off the default branch are different
    claims, and a diagnosis built on the second must not read like one built on the
    first."""
    repo = await with_workload(
        a_workload(deployed_commit="9f2c1ab", commit_source="default_branch")
    )

    assert await deployed_repo(config, repo, TENANT) == Deployment(
        PLATFORM, "9f2c1ab", CommitSource.DEFAULT_BRANCH
    )


async def test_a_commit_the_map_supplied_claims_no_source(config):
    repo = mapped(a_service_entry(name="payments-api"))

    assert (await deployed_repo(config, repo, "payments-api")).commit_source is None
