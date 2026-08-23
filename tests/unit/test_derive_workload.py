"""The derivation rule: what an image is allowed to conclude, and what it refuses to.

The events are the captured ones, mutated only where a test needs an image the
seed cannot account for — and even then the image string is one that was really
running in that same StatefulSet (`alpine/openssl`, its init container).
"""

import pytest

from tests.conftest import TENANT, declaring, running_image
from triage.mapping.derive import derive_workload
from triage.mapping.seed import load_seed
from triage.schemas.system_map import MappingOutcome


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
