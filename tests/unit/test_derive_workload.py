"""The derivation rule: what an image is allowed to conclude, and what it refuses to.

The events are the captured ones, mutated only where a test needs an image the
seed cannot account for — and even then the image string is one that was really
running in that same StatefulSet (`alpine/openssl`, its init container).
"""

import pytest

from tests.conftest import TENANT, captured, declaring, running_image
from triage.mapping.derive import derive_workload
from triage.mapping.seed import load_seed
from triage.schemas.common import Unknown
from triage.schemas.system_map import MappingOutcome, MappingSource, Tenancy

DIGEST = "sha256:2e15f697553acdbdd13ec687080f1b600d531b504b73603dede0bda606d1d87b"


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture
def config():
    return declaring("github.com/zeenea/platform", "github.com/zeenea/platform-infra")


def test_an_image_the_seed_does_not_name_is_a_stated_failure(config, seed):
    event = running_image("alpine/openssl")

    derivation = derive_workload(config, seed, TENANT, [event])

    assert derivation.outcome is MappingOutcome.UNRESOLVED_IMAGE
    assert derivation.entry is None
    assert "openssl" in derivation.reason


def test_an_unknown_image_is_never_matched_onto_the_nearest_repository_name(config, seed):
    """`platform-api` is a real repository, is not in the seed, and is exactly what a
    prefix match would file under `platform` — sending every analysis to the wrong tree."""
    event = running_image("097607883991.dkr.ecr.eu-west-3.amazonaws.com/platform-api")

    derivation = derive_workload(config, seed, TENANT, [event])

    assert derivation.outcome is MappingOutcome.UNRESOLVED_IMAGE
    assert "platform-api" in derivation.reason


def test_the_failure_names_the_image_reference_it_saw(config, seed):
    event = running_image("ghcr.io/other/thing:2.0")
    assert "ghcr.io/other/thing:2.0" in derive_workload(config, seed, TENANT, [event]).reason


def test_a_seed_repository_that_cannot_run_under_this_name_is_a_conflict_not_a_failure(
    config, seed
):
    """`platform-infra` is in the seed and is not mono-tenant, so a tenant name for it
    is a mapping to check — a different problem from an image nobody recognises."""
    event = running_image("097607883991.dkr.ecr.us-east-1.amazonaws.com/platform-infra")

    derivation = derive_workload(config, seed, TENANT, [event])

    assert derivation.outcome is MappingOutcome.CONFLICT
    assert derivation.entry is None


def test_the_deployed_commit_is_unknown_when_the_tag_is_a_build_number(config, seed):
    """What the captured tenant was actually running: `image_tag:501`."""
    events = captured("events_service")["data"]

    entry = derive_workload(config, seed, TENANT, events).entry

    assert isinstance(entry.deployed_commit, Unknown)
    assert "'501' is not a commit" in entry.deployed_commit.reason
    assert "was found" in entry.deployed_commit.reason


@pytest.mark.parametrize("tag", ["9f2c1ab", "sha-9f2c1ab", "SHA-9F2C1AB", "git-9f2c1ab"])
def test_a_tag_that_carries_a_commit_is_recorded_as_the_deployed_commit(config, seed, tag):
    event = running_image(f"097607883991.dkr.ecr.us-east-1.amazonaws.com/platform:{tag}")

    entry = derive_workload(config, seed, TENANT, [event]).entry

    assert entry.deployed_commit == "9f2c1ab"


@pytest.mark.parametrize("tag", ["501", "1234567", "latest", "1.2.3", "v2"])
def test_a_tag_that_carries_no_commit_leaves_it_unknown(config, seed, tag):
    """Seven digits is a plausible build number and a plausible short SHA; reading it
    as a commit would send an analysis to a commit that does not exist."""
    event = running_image(f"097607883991.dkr.ecr.us-east-1.amazonaws.com/platform:{tag}")

    entry = derive_workload(config, seed, TENANT, [event]).entry

    assert isinstance(entry.deployed_commit, Unknown)


def test_an_image_pinned_only_by_digest_says_it_carries_no_tag(config, seed):
    event = running_image(f"097607883991.dkr.ecr.us-east-1.amazonaws.com/platform@{DIGEST}")

    entry = derive_workload(config, seed, TENANT, [event]).entry

    assert isinstance(entry.deployed_commit, Unknown)
    assert "carries no tag" in entry.deployed_commit.reason


def test_a_service_with_no_image_event_falls_back_to_the_serves_pattern(seed):
    """The M3 stopgap, kept: a tenant that has been quiet for a week is still mapped."""
    config = declaring("github.com/zeenea/platform", serves=("plt-*",))

    derivation = derive_workload(config, seed, "plt-merck-qa", [])

    assert derivation.outcome is MappingOutcome.MAPPED
    assert derivation.entry.source is MappingSource.PATTERN
    assert derivation.entry.repo_url == "github.com/zeenea/platform"
    assert derivation.entry.image is None


def test_a_pattern_mapping_still_takes_its_tenancy_from_the_seed(seed):
    config = declaring("github.com/zeenea/platform", serves=("plt-*",))
    entry = derive_workload(config, seed, "plt-merck-qa", []).entry
    assert entry.tenancy is Tenancy.MONO_TENANT
    assert entry.iac_repo == "platform-infra"


def test_a_pattern_mapping_never_claims_to_know_the_deployed_commit(seed):
    config = declaring("github.com/zeenea/platform", serves=("plt-*",))
    entry = derive_workload(config, seed, "plt-merck-qa", []).entry
    assert isinstance(entry.deployed_commit, Unknown)
    assert "naming pattern, not an observation" in entry.deployed_commit.reason


def test_a_pattern_onto_a_repository_the_seed_does_not_name_leaves_tenancy_unknown(seed):
    config = declaring("github.com/org/payments-api", serves=("payments-*",))

    entry = derive_workload(config, seed, "payments-api", []).entry

    assert isinstance(entry.tenancy, Unknown)
    assert "payments-api" in entry.tenancy.reason


def test_with_neither_an_image_nor_a_pattern_there_is_no_mapping(config, seed):
    derivation = derive_workload(config, seed, "ledger-api", [])

    assert derivation.outcome is MappingOutcome.NOT_MAPPED
    assert derivation.entry is None
    assert "no serves pattern" in derivation.reason
