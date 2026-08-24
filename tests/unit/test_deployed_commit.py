"""Which commit a workload is running, once the image tag has failed to say.

The captured tenant is the case: `platform` at image tag `501`, a build number
that is not a commit and *is* a tag in GitHub. Everything here is offline — the
GitHub reads go through the protocol's fake, which is what the mapping pass
holds.
"""

import re

import pytest
from pydantic import ValidationError

from tests.conftest import TENANT, a_workload, declaring
from triage.config import Repo, RepoKind
from triage.integrations.github import FakeGitHubClient
from triage.mapping.commits import with_deployed_commit
from triage.schemas.common import Unknown
from triage.schemas.system_map import CommitSource

PLATFORM = "github.com/zeenea/platform"
PLATFORM_INFRA = "github.com/zeenea/platform-infra"
COMMIT = "9f2c1ab8b0e3d4f5a6b7c8d9e0f1a2b3c4d5e6f7"


@pytest.fixture
def config():
    return declaring(PLATFORM)


def github(**overrides: object) -> FakeGitHubClient:
    return FakeGitHubClient(**overrides)  # type: ignore[arg-type]


async def test_a_tag_github_knows_resolves_to_the_commit_it_names(config):
    client = github(tags={(PLATFORM, "501"): COMMIT})

    entry = await with_deployed_commit(client, config, a_workload())

    assert entry.deployed_commit == COMMIT
    assert entry.commit_source is CommitSource.GITHUB_TAG
    assert client.tag_lookups == [(PLATFORM, "501")]


async def test_the_commit_the_image_tag_already_carries_needs_no_github_read(config):
    client = github(tags={(PLATFORM, "sha-9f2c1ab"): COMMIT})

    entry = await with_deployed_commit(
        client,
        config,
        a_workload(deployed_commit="9f2c1ab", commit_source=CommitSource.IMAGE_TAG),
    )

    assert entry.deployed_commit == "9f2c1ab"
    assert entry.commit_source is CommitSource.IMAGE_TAG
    assert client.tag_lookups == []


async def test_a_workload_mapped_from_a_pattern_is_left_as_it_is(config):
    """It observed no image, so there is no build to look up and nothing to ask about."""
    client = github()

    entry = await with_deployed_commit(client, config, a_workload(source="pattern", image=None))

    assert isinstance(entry.deployed_commit, Unknown)
    assert entry.commit_source is None
    assert client.tag_lookups == []


async def test_the_service_and_the_tag_are_named_when_github_has_no_such_tag(config):
    entry = await with_deployed_commit(github(), config, a_workload())

    assert isinstance(entry.deployed_commit, Unknown)
    assert "501" in entry.deployed_commit.reason
    assert TENANT in entry.deployed_commit.reason


async def test_a_repository_whose_tags_are_spelled_differently_declares_the_relationship():
    """`config.yaml` says `v{tag}`; nothing else is tried, because a tag invented by
    guessing points somewhere specific and wrong."""
    config = declaring(PLATFORM, tag_template="v{tag}")
    client = github(tags={(PLATFORM, "v501"): COMMIT})

    entry = await with_deployed_commit(client, config, a_workload())

    assert entry.deployed_commit == COMMIT
    assert client.tag_lookups == [(PLATFORM, "v501")]


async def test_a_declared_spelling_that_does_not_exist_is_not_retried_as_the_image_tag():
    config = declaring(PLATFORM, tag_template="build-{tag}")
    client = github(tags={(PLATFORM, "501"): COMMIT})

    entry = await with_deployed_commit(client, config, a_workload())

    assert isinstance(entry.deployed_commit, Unknown)
    assert client.tag_lookups == [(PLATFORM, "build-501")]


def test_a_tag_template_that_does_not_place_the_image_tag_is_refused():
    with pytest.raises(ValidationError, match=re.escape("{tag}")):
        Repo(url=PLATFORM, team="platform", kind=RepoKind.APPLICATION, tag_template="release")


async def test_a_repository_config_does_not_declare_is_not_looked_up_at_all():
    """The image names a repository; config.yaml names a *GitHub* repository, and the
    two are not the same string — the image is `platform` where the remote is
    `zeenea/datacatalog`. With no declaration there is no remote to ask."""
    client = github(tags={(PLATFORM, "501"): COMMIT})

    entry = await with_deployed_commit(client, declaring(PLATFORM_INFRA), a_workload())

    assert client.tag_lookups == []
    assert isinstance(entry.deployed_commit, Unknown)
    assert "config.yaml" in entry.deployed_commit.reason
    assert "'platform'" in entry.deployed_commit.reason


async def test_the_undeclared_repository_is_not_blamed_on_its_tag():
    entry = await with_deployed_commit(github(), declaring(PLATFORM_INFRA), a_workload())

    assert isinstance(entry.deployed_commit, Unknown)
    assert "501" not in entry.deployed_commit.reason
