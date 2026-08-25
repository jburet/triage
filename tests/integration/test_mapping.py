"""End-to-end runs of the service-mapping graph, over the captured incident.

The tenant here is the one the milestone exists for: `plt-hcl-software-uat` is a
customer of `platform`, alerts under its own name, and is in no repository's
system-map entry. Assertions are on what a pass *left behind* — the workload
rows, and the line it wrote about every service it could not map.
"""

from datetime import UTC, datetime, timedelta

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
from triage.schemas.common import Feature
from triage.schemas.errors import ErrorGroup, ErrorTrack, Novelty
from triage.schemas.signal import Signal
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


async def test_the_pass_reports_every_service_it_attributed_and_how(zeenea):
    """M6 4.3: the pass says what it mapped from the image that was running, what
    it only guessed from a name, and what it could not map at all."""
    deps = deps_for(zeenea, github=FakeGitHubClient(trees={PLATFORM_INFRA: INFRA_TREE}))

    state = await run(deps)

    report = state["report"]
    assert report.services == 1
    assert [line.service for line in report.by_image] == [TENANT]
    assert (report.by_pattern, report.unmapped, report.conflicting) == ([], [], [])
    assert report.without_chart == []


async def test_the_report_goes_to_the_platform_channel(zeenea):
    """An unmapped production workload is Triage's own gap, not the owning team's."""
    deps = deps_for(zeenea, github=FakeGitHubClient(trees={PLATFORM_INFRA: INFRA_TREE}))

    await run(deps)

    (message,) = deps.slack.messages
    assert message.channel == zeenea.platform_channel()
    assert TENANT in message.text
    assert "running image" in message.text


async def test_a_service_mapped_only_by_a_name_pattern_is_counted_apart(zeenea):
    """The stopgap and the derivation are different facts and read as two lines."""
    deps = deps_for(declaring(PLATFORM, serves=("ledger-*",)))

    state = await run(deps, services=["ledger-api"])

    assert [line.service for line in state["report"].by_pattern] == ["ledger-api"]
    assert state["report"].by_image == []


async def test_a_service_the_pass_could_not_map_is_named_in_the_report(zeenea):
    deps = build_deps(zeenea, datadog=datadog_running("ghcr.io/other/thing:2.0"))

    state = await run(deps)

    (line,) = state["report"].unmapped
    assert line.service == TENANT
    assert "ghcr.io/other/thing:2.0" in line.detail


async def test_a_mapping_the_seed_forbids_is_reported_as_a_conflict(zeenea):
    """A multi-tenant repository running under a name that is not its own (1.4)."""
    deps = build_deps(
        zeenea, datadog=datadog_running("097607883991.dkr.ecr.us-east-1.amazonaws.com/studio:9")
    )

    state = await run(deps)

    assert [line.service for line in state["report"].conflicting] == [TENANT]
    assert state["report"].unmapped == []


async def test_a_workload_whose_chart_was_not_found_is_reported_as_mapped_and_incomplete(zeenea):
    """The fifth column: `platform-infra` was resolved and the chart inside it was
    not, so an iac_analysis there is back to selecting by glob (3.1)."""
    deps = deps_for(zeenea, github=FakeGitHubClient(branch_commits={PLATFORM: COMMIT}))

    state = await run(deps)

    report = state["report"]
    assert [line.service for line in report.by_image] == [TENANT]
    assert [line.service for line in report.without_chart] == [TENANT]


async def test_a_pass_that_derived_nothing_posts_nothing(zeenea):
    deps = deps_for(zeenea)

    await graph.ainvoke({"services": []}, config=run_config(deps))

    assert deps.slack.messages == []


REAL_INFRA_TREE = [
    "README.md",
    "terraform/core-eks/main.tf",
    "terraform/database/main.tf",
    "terraform/eks_module/eks.tf",
    "terraform/eks_module/volumes.tf",
]
"""`platform-infra` as the 2026-08-24 pass found it: no path names the workload."""


def declaring_where_the_workload_is(config, **defines):
    repos = [
        repo.model_copy(update={"defines": defines}) if repo.url == PLATFORM_INFRA else repo
        for repo in config.repos
    ]
    return config.model_copy(update={"repos": repos})


async def test_the_declared_module_is_where_the_workload_is_defined(zeenea):
    """ADR-0021. Without the declaration this repository's listing names no
    workload at all, and the pass reports a chart it could not find."""
    deps = deps_for(
        declaring_where_the_workload_is(zeenea, platform=["terraform/eks_module/*"]),
        github=FakeGitHubClient(trees={PLATFORM_INFRA: REAL_INFRA_TREE}),
    )

    await run(deps)

    entry = deps.repo.workloads[TENANT]
    assert "terraform/eks_module/eks.tf" in entry.iac_paths
    assert "terraform/database/main.tf" not in entry.iac_paths


async def test_a_declaration_about_another_workload_is_not_this_ones(zeenea):
    """One IaC repository provisions several workloads; a declaration names which."""
    deps = deps_for(
        declaring_where_the_workload_is(zeenea, scanner=["terraform/eks_module/*"]),
        github=FakeGitHubClient(trees={PLATFORM_INFRA: REAL_INFRA_TREE}),
    )

    await run(deps)

    assert deps.repo.workloads[TENANT].iac_paths == []


class TestWhatAPassCoversByDefault:
    """Given no service names, a pass covers what Triage has needed a mapping for.

    That was read from the signals table alone — services that *alerted*. A
    tenant raising a code exception never alerts, so on 2026-08-25 the default
    pass returned zero services while seventy tenants were raising exceptions,
    and every F2 report then said no deployed commit was known for its tenant.
    """

    async def _group_in(self, deps, service: str, *, last_seen: datetime) -> None:
        await deps.repo.upsert_error_group(
            ErrorGroup.model_validate(
                {
                    "key": f"java.lang.NullPointerException|Property.scala|get|{service}",
                    "error_type": "java.lang.NullPointerException",
                    "file_path": "zeenea.repository.orientdb.mapping.Property.scala",
                    "function_name": "get",
                    "repository": "platform",
                    "track": ErrorTrack.TRACE,
                    "novelty": Novelty.NEW,
                    "services": {service: 40},
                    "occurrences": 40,
                    "first_seen": last_seen,
                    "last_seen": last_seen,
                }
            )
        )

    async def test_a_tenant_that_only_raised_an_exception_is_covered(self, zeenea):
        deps = deps_for(zeenea)
        await self._group_in(deps, TENANT, last_seen=datetime.now(UTC))

        state = await graph.ainvoke({}, config=run_config(deps))

        assert TENANT in state["targets"]

    async def test_a_tenant_whose_group_is_older_than_the_lookback_is_not(self, zeenea):
        deps = deps_for(zeenea)
        await self._group_in(deps, TENANT, last_seen=datetime.now(UTC) - timedelta(days=90))

        state = await graph.ainvoke({}, config=run_config(deps))

        assert TENANT not in state["targets"]

    async def test_a_tenant_that_both_alerted_and_raised_is_named_once(self, zeenea):
        deps = deps_for(zeenea)
        await self._group_in(deps, TENANT, last_seen=datetime.now(UTC))
        await deps.repo.save_signal(Signal(feature=Feature.F1, source="datadog", service=TENANT))

        state = await graph.ainvoke({}, config=run_config(deps))

        assert state["targets"].count(TENANT) == 1
