"""The pure rules of the mapping: who claims a repository, and what may be named after what."""

import pytest

from tests.conftest import declaring
from triage.mapping.resolve import naming_conflict, unclaimed
from triage.mapping.seed import load_seed, seed_for


def test_a_seed_repository_no_team_declares_is_unclaimed():
    seed = load_seed()
    config = declaring("github.com/zeenea/platform")

    missing = unclaimed(config, seed)

    assert "platform" not in missing
    assert "studio" in missing
    assert len(missing) == len(seed) - 1


def test_nothing_is_invented_for_an_unclaimed_repository():
    """Its team stays unknown: config.yaml is where ownership is stated."""
    config = declaring("github.com/zeenea/platform")
    assert config.repo_named("studio") is None


def test_a_repository_is_claimed_by_the_url_it_is_declared_under():
    config = declaring("github.com/zeenea/platform")
    declared = config.repo_named("platform")
    assert declared is not None
    assert declared.url == "github.com/zeenea/platform"


def test_a_repository_whose_name_only_prefixes_a_declared_one_is_not_claimed():
    """`platform-infra` is a different repository from `platform`, and a suffix match
    that conflated them would point every platform analysis at the Terraform."""
    config = declaring("github.com/zeenea/platform-infra")
    assert config.repo_named("platform") is None


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def test_a_mono_tenant_repository_may_run_under_a_customers_name(seed):
    """The reason this milestone exists: `plt-hcl-software-uat` is a tenant of `platform`."""
    platform = seed_for(seed, "platform")
    assert naming_conflict(platform, "plt-hcl-software-uat") is None


def test_a_multi_tenant_repository_running_under_another_name_is_a_conflict(seed):
    studio = seed_for(seed, "studio")
    reason = naming_conflict(studio, "studio-merck")
    assert reason is not None
    assert "studio-merck" in reason
    assert "multi_tenant" in reason


def test_a_multi_tenant_repository_running_under_its_own_name_is_no_conflict(seed):
    studio = seed_for(seed, "studio")
    assert naming_conflict(studio, "studio") is None


def test_a_repository_with_no_tenancy_model_gets_no_licence_to_differ(seed):
    """`zeenea-api-gateway` is a routing layer, not a mono-tenant workload: a second
    name for it is a mapping to check, not a tenant."""
    gateway = seed_for(seed, "zeenea-api-gateway")
    assert naming_conflict(gateway, "zeenea-api-gateway-merck") is not None


def test_a_repository_whose_remote_is_not_named_after_its_image_is_still_claimed():
    """The image says `platform`; the remote is `zeenea/datacatalog`.

    M6 2.10 refuses a GitHub read for a repository config.yaml does not declare,
    and the URL basename is what decides that. On the real mapping pass the
    workload resolved to the repository `platform`, matched nothing, and lost its
    commit for a spelling — which is the failure 2.10 exists to report, not one it
    should be provoking.
    """
    config = declaring("github.com/zeenea/datacatalog", image_name="platform")

    assert config.repo_named("platform") is not None
    assert config.repo_named("datacatalog") is None


def test_the_url_basename_still_claims_a_repository_that_declares_no_image_name():
    config = declaring("github.com/org/payments-api")

    assert config.repo_named("payments-api") is not None
