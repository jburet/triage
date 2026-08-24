"""Which commit a workload is running, once the image tag has failed to say.

The captured tenant is the case: `platform` at image tag `501`, a build number
that is not a commit and *is* a tag in GitHub. Everything here is offline — the
GitHub reads go through the protocol's fake, which is what the mapping pass
holds.
"""

import pytest

from tests.conftest import TENANT, a_workload, declaring
from triage.integrations.github import FakeGitHubClient
from triage.mapping.commits import with_deployed_commit
from triage.schemas.common import Unknown
from triage.schemas.system_map import CommitSource

PLATFORM = "github.com/zeenea/platform"
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
