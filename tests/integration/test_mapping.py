"""End-to-end runs of the service-mapping graph, over the captured incident.

The tenant here is the one the milestone exists for: `plt-hcl-software-uat` is a
customer of `platform`, alerts under its own name, and is in no repository's
system-map entry. Assertions are on what a pass *left behind* — the workload
rows, and the line it wrote about every service it could not map.
"""

from datetime import UTC, datetime

import pytest

from tests.conftest import (
    TENANT,
    build_deps,
    datadog_running,
    declaring,
    fake_datadog_over_days,
    run_config,
    running_image,
)
from triage.graphs.mapping import graph
from triage.integrations.datadog import FakeDatadogClient
from triage.integrations.github import FakeGitHubClient, GitHubError
from triage.schemas.system_map import CommitSource, MappingOutcome, MappingSource, Tenancy

PLATFORM = "github.com/zeenea/platform"
PLATFORM_INFRA = "github.com/zeenea/platform-infra"
DIGEST = "sha256:2e15f697553acdbdd13ec687080f1b600d531b504b73603dede0bda606d1d87b"
COMMIT = "9f2c1ab8b0e3d4f5a6b7c8d9e0f1a2b3c4d5e6f7"


@pytest.fixture
def zeenea():
    return declaring(PLATFORM, PLATFORM_INFRA)


def two_tenants():
    """The captured tenant, plus a second customer of the same repository."""
    replay = dict(fake_datadog_over_days().responses)
    replay["events"] = {
        **replay["events"],
        "service:plt-merck-qa": {
            "data": [running_image("097607883991.dkr.ecr.us-east-1.amazonaws.com/platform:502")]
        },
    }
    return FakeDatadogClient(responses=replay, wide=replay)


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


async def test_the_build_number_the_tenant_runs_becomes_a_commit_through_github(zeenea):
    """`501` is not a commit and is a tag: the whole reason Phase 2b exists."""
    deps = deps_for(zeenea, github=FakeGitHubClient(tags={(PLATFORM, "501"): COMMIT}))

    await run(deps)

    entry = deps.repo.workloads[TENANT]
    assert entry.deployed_commit == COMMIT
    assert entry.commit_source is CommitSource.GITHUB_TAG


async def test_a_pass_that_knows_when_the_incident_fired_reads_the_branch_as_it_stood(zeenea):
    fired = datetime(2026, 8, 22, 0, 43, tzinfo=UTC)
    deps = deps_for(zeenea, github=FakeGitHubClient(branch_commits={PLATFORM: COMMIT}))

    await graph.ainvoke({"services": [TENANT], "at": fired}, config=run_config(deps))

    assert deps.github.branch_reads == [(PLATFORM, fired)]
    assert deps.repo.workloads[TENANT].commit_read_at == fired


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


INFRA_TREE = [
    "README.md",
    "helm/zeenea-platform/Chart.yaml",
    "helm/zeenea-platform/values.yaml",
    "helm/zeenea-platform/templates/statefulset.yaml",
    "helm/zeenea-connector/values.yaml",
    "modules/eks/main.tf",
]


async def test_the_entry_says_where_in_the_iac_repository_this_workload_is_defined(zeenea):
    """M6 3.1: resolving `platform-infra` is only half a mapping — the probe
    timeouts are in the chart, and nothing said where the chart was."""
    deps = deps_for(zeenea, github=FakeGitHubClient(trees={PLATFORM_INFRA: INFRA_TREE}))

    await run(deps)

    entry = deps.repo.workloads[TENANT]
    assert entry.iac_paths[0] == "helm/zeenea-platform/values.yaml"
    assert "helm/zeenea-connector/values.yaml" not in entry.iac_paths


async def test_the_iac_repository_is_listed_once_however_many_tenants_run_it(zeenea):
    """One chart, sixty-odd tenants: a listing per tenant is how a pass earns a
    rate limit reaching the same answer it already had."""
    deps = build_deps(
        zeenea,
        datadog=two_tenants(),
        github=FakeGitHubClient(trees={PLATFORM_INFRA: INFRA_TREE}),
    )

    await run(deps, services=[TENANT, "plt-merck-qa"])

    assert deps.github.tree_reads == [PLATFORM_INFRA]
    assert deps.repo.workloads["plt-merck-qa"].iac_paths == deps.repo.workloads[TENANT].iac_paths


async def test_a_listing_github_will_not_give_leaves_the_mapping_standing(zeenea):
    deps = deps_for(zeenea, github=FakeGitHubClient(branch_commits={PLATFORM: COMMIT}))

    state = await run(deps)

    assert state["derivations"][0].outcome is MappingOutcome.MAPPED
    assert deps.repo.workloads[TENANT].iac_paths == []


async def test_a_repository_config_does_not_declare_is_mapped_without_a_url(zeenea):
    """The mapping is still right; nobody has said where the code lives."""
    deps = deps_for(declaring(PLATFORM_INFRA))
    await run(deps)

    entry = deps.repo.workloads[TENANT]
    assert entry.repository == "platform"
    assert entry.repo_url is None


async def test_an_undeclared_repository_costs_no_github_read_and_is_still_mapped(zeenea):
    """The mapping holds; nobody has said where the code lives, so nothing is asked."""
    deps = deps_for(declaring(PLATFORM_INFRA), github=FakeGitHubClient())

    await run(deps)

    assert deps.github.tag_lookups == []
    assert deps.repo.workloads[TENANT].repository == "platform"


async def test_a_service_with_no_events_at_all_is_reported_rather_than_dropped(zeenea):
    deps = deps_for(zeenea)

    state = await run(deps, services=["ledger-api"])

    (derivation,) = state["derivations"]
    assert derivation.outcome is MappingOutcome.NOT_MAPPED
    assert "ledger-api" in derivation.reason
    assert state["entries_written"] == 0
    assert deps.repo.workloads == {}


async def test_one_unreachable_repository_does_not_cost_the_others_their_mapping(zeenea):
    """A rate limit is not a mapping failure: the repository and the digest still
    stand, and only the commit is missing, with the failure as its reason."""
    deps = build_deps(
        zeenea,
        datadog=two_tenants(),
        github=FakeGitHubClient(error=GitHubError("403: API rate limit exceeded")),
    )

    state = await run(deps, services=[TENANT, "plt-merck-qa"])

    assert [derivation.outcome for derivation in state["derivations"]] == [
        MappingOutcome.MAPPED,
        MappingOutcome.MAPPED,
    ]
    assert state["entries_written"] == 2
    assert "rate limit" in deps.repo.workloads[TENANT].deployed_commit.reason


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


async def test_an_image_no_repository_is_named_after_writes_nothing_and_says_so(zeenea):
    deps = build_deps(zeenea, datadog=datadog_running("ghcr.io/other/thing:2.0"))

    state = await run(deps)

    (derivation,) = state["derivations"]
    assert derivation.outcome is MappingOutcome.UNRESOLVED_IMAGE
    assert "ghcr.io/other/thing:2.0" in derivation.reason
    assert deps.repo.workloads == {}
    assert state["entries_written"] == 0


async def test_a_second_pass_over_an_unchanged_digest_rewrites_nothing(zeenea):
    deps = deps_for(zeenea)
    await run(deps)

    state = await run(deps)

    (derivation,) = state["derivations"]
    assert derivation.outcome is MappingOutcome.UNCHANGED
    assert DIGEST in derivation.reason
    assert state["entries_written"] == 0
    assert deps.repo.workloads[TENANT].image_digest == DIGEST


async def test_a_second_pass_over_an_unchanged_digest_asks_github_nothing(zeenea):
    """2.5's rule, one level down: the same digest is the same build, so the commit
    on record is still the commit and a second read would spend a rate limit to
    learn what is already written."""
    deps = deps_for(zeenea, github=FakeGitHubClient(tags={(PLATFORM, "501"): COMMIT}))
    await run(deps)

    state = await run(deps)

    assert deps.github.tag_lookups == [(PLATFORM, "501")]
    assert state["derivations"][0].outcome is MappingOutcome.UNCHANGED
    assert deps.repo.workloads[TENANT].deployed_commit == COMMIT


async def test_a_digest_that_moved_is_written_over_the_old_one(zeenea):
    moved = "sha256:" + "a" * 64
    deps = deps_for(zeenea)
    await run(deps)

    deps = build_deps(
        zeenea,
        repo=deps.repo,
        datadog=datadog_running(
            f"097607883991.dkr.ecr.us-east-1.amazonaws.com/platform:502@{moved}"
        ),
    )
    state = await run(deps)

    assert state["derivations"][0].outcome is MappingOutcome.MAPPED
    assert state["entries_written"] == 1
    assert deps.repo.workloads[TENANT].image_digest == moved
