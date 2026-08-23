"""End-to-end runs of the service-mapping graph, over the captured incident.

The tenant here is the one the milestone exists for: `plt-hcl-software-uat` is a
customer of `platform`, alerts under its own name, and is in no repository's
system-map entry. Assertions are on what a pass *left behind* — the workload
rows, and the line it wrote about every service it could not map.
"""

import pytest

from tests.conftest import build_deps, declaring, fake_datadog_over_days, run_config
from triage.graphs.mapping import graph
from triage.schemas.system_map import MappingOutcome, MappingSource, Tenancy

TENANT = "plt-hcl-software-uat"
PLATFORM = "github.com/zeenea/platform"
PLATFORM_INFRA = "github.com/zeenea/platform-infra"
DIGEST = "sha256:2e15f697553acdbdd13ec687080f1b600d531b504b73603dede0bda606d1d87b"


@pytest.fixture
def zeenea():
    return declaring(PLATFORM, PLATFORM_INFRA)


def deps_for(config, **overrides):
    return build_deps(config, datadog=fake_datadog_over_days(), **overrides)


async def run(deps, services=(TENANT,)):
    return await graph.ainvoke({"services": list(services)}, config=run_config(deps))


async def test_a_tenant_is_mapped_to_the_repository_its_running_image_names(zeenea):
    deps = deps_for(zeenea)

    state = await run(deps)

    (derivation,) = state["derivations"]
    assert derivation.outcome is MappingOutcome.MAPPED
    entry = deps.repo.workloads[TENANT]
    assert entry.repository == "platform"
    assert entry.repo_url == PLATFORM
    assert entry.source is MappingSource.IMAGE
    assert state["entries_written"] == 1


async def test_the_entry_records_the_digest_that_was_running(zeenea):
    deps = deps_for(zeenea)
    await run(deps)
    assert deps.repo.workloads[TENANT].image_digest == DIGEST


async def test_the_seed_supplies_the_tenancy_and_the_iac_repository(zeenea):
    """Neither is discoverable from the cluster: this is what the document is for."""
    deps = deps_for(zeenea)
    await run(deps)

    entry = deps.repo.workloads[TENANT]
    assert entry.tenancy is Tenancy.MONO_TENANT
    assert entry.iac_repo == "platform-infra"
    assert entry.iac_repo_url == PLATFORM_INFRA


async def test_a_repository_config_does_not_declare_is_mapped_without_a_url(zeenea):
    """The mapping is still right; nobody has said where the code lives."""
    deps = deps_for(declaring(PLATFORM_INFRA))
    await run(deps)

    entry = deps.repo.workloads[TENANT]
    assert entry.repository == "platform"
    assert entry.repo_url is None


async def test_a_service_with_no_events_at_all_is_reported_rather_than_dropped(zeenea):
    deps = deps_for(zeenea)

    state = await run(deps, services=["ledger-api"])

    (derivation,) = state["derivations"]
    assert derivation.outcome is MappingOutcome.NOT_MAPPED
    assert "ledger-api" in derivation.reason
    assert state["entries_written"] == 0
    assert deps.repo.workloads == {}


async def test_the_pass_lists_seed_repositories_no_team_claims(zeenea):
    deps = deps_for(zeenea)
    state = await run(deps)
    assert "platform" not in state["unclaimed"]
    assert "studio" in state["unclaimed"]


async def test_one_query_per_service_and_nothing_else(zeenea):
    """Read-only, and no model call anywhere on this path."""
    deps = deps_for(zeenea)
    await run(deps)

    assert deps.datadog.queries_for("events") == [f"service:{TENANT}"]
    assert deps.llm.calls == []
