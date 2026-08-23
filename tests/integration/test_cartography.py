"""End-to-end runs of the cartography graph.

Assertions are on what the run *left behind* — the rows in the map, the Slack
messages, the requests the runner was given — rather than on summary prose,
which is canned here. Summary quality is measured by ``evals/cartography.py``.
"""

import pytest

from tests.conftest import (
    a_repo_summary,
    a_terraform_summary,
    an_analysis_result,
    build_deps,
    canned_runner,
    run_config,
)
from triage.analysis.runner import FakeAnalysisRunner
from triage.db.repo import InMemoryRepository
from triage.graphs.cartography import graph
from triage.schemas import AnalysisKind, AnalysisResult, SystemMapKind

PAYMENTS = "github.com/org/payments-api"
INFRA = "github.com/org/infra"

FULL_RUN = {
    "repos": [
        {"url": PAYMENTS, "commit": "9f2c1ab"},
        {"url": INFRA, "commit": "abc1234"},
    ]
}


async def run(deps, state=None):
    return await graph.ainvoke(dict(state or FULL_RUN), config=run_config(deps))


async def test_a_full_pass_persists_one_row_per_service_and_module(config):
    deps = build_deps(config, runner=canned_runner())
    state = await run(deps)

    assert state["entries_written"] == 2
    assert {(entry.kind, entry.name) for entry in deps.repo.system_map.values()} == {
        (SystemMapKind.SERVICE, "payments-api"),
        (SystemMapKind.TERRAFORM_MODULE, "modules/payments"),
    }


async def test_rows_carry_the_owning_team_and_the_summarised_commit(config):
    deps = build_deps(config, runner=canned_runner())
    await run(deps)

    service = deps.repo.system_map[(SystemMapKind.SERVICE, "payments-api")]
    module = deps.repo.system_map[(SystemMapKind.TERRAFORM_MODULE, "modules/payments")]

    assert (service.team, service.source_commit) == ("payments", "9f2c1ab")
    assert (module.team, module.source_commit) == ("platform", "abc1234")


async def test_each_repository_is_summarised_at_the_commit_it_was_given(config):
    runner = canned_runner()
    deps = build_deps(config, runner=runner)
    await run(deps)

    asked = {(request.repo_url, request.kind, request.commit) for request in runner.requests}
    assert asked == {
        (PAYMENTS, AnalysisKind.SUMMARIZE_REPO, "9f2c1ab"),
        (INFRA, AnalysisKind.SUMMARIZE_TERRAFORM, "abc1234"),
    }


async def test_an_unpinned_repository_is_summarised_at_the_tip_and_records_no_commit(config):
    runner = canned_runner()
    deps = build_deps(config, runner=runner)
    await run(deps, {"repos": [{"url": PAYMENTS}]})

    assert runner.requests[0].commit == "HEAD"
    assert deps.repo.system_map[(SystemMapKind.SERVICE, "payments-api")].source_commit is None


async def test_no_repos_given_means_every_repository_config_declares(config):
    runner = canned_runner()
    deps = build_deps(config, runner=runner)
    await run(deps, {})

    assert {request.repo_url for request in runner.requests} == {PAYMENTS, INFRA}


async def test_re_running_over_the_same_commits_updates_in_place(config):
    repo = InMemoryRepository()
    deps = build_deps(config, runner=canned_runner(), repo=repo)

    await run(deps)
    await run(build_deps(config, runner=canned_runner(), repo=repo))

    assert len(repo.system_map) == 2


async def test_re_running_at_a_new_commit_replaces_the_recorded_commit(config):
    repo = InMemoryRepository()
    await run(build_deps(config, runner=canned_runner(), repo=repo))
    await run(
        build_deps(config, runner=canned_runner(), repo=repo),
        {"repos": [{"url": PAYMENTS, "commit": "ffffff0"}]},
    )

    entry = await repo.system_map_for_service("payments-api")
    assert entry is not None
    assert entry.source_commit == "ffffff0"
    assert len(repo.system_map) == 2


async def test_the_map_answers_what_a_location_is_built_from(config):
    deps = build_deps(config, runner=canned_runner())
    await run(deps)

    entry = await deps.repo.system_map_for_service("payments-api")
    assert entry is not None
    assert entry.repo_url == PAYMENTS
    assert entry.team == "payments"
    assert entry.source_commit == "9f2c1ab"
    assert [point.path for point in entry.summary.entry_points] == [
        "src/payments/main.py",
        "src/payments/worker.py",
    ]
    assert [resource.address for resource in entry.terraform_resources] == [
        "module.payments.aws_db_instance.primary"
    ]


async def test_the_last_summarised_commit_is_readable_per_repository(config):
    deps = build_deps(config, runner=canned_runner())
    await run(deps)

    assert await deps.repo.last_summarised_commit(PAYMENTS) == "9f2c1ab"
    assert await deps.repo.last_summarised_commit(INFRA) == "abc1234"


async def test_a_run_without_terraform_keeps_the_resources_already_mapped(config):
    repo = InMemoryRepository()
    await run(build_deps(config, runner=canned_runner(), repo=repo))
    await run(
        build_deps(config, runner=canned_runner(), repo=repo),
        {"repos": [{"url": PAYMENTS, "commit": "ffffff0"}]},
    )

    entry = await repo.system_map_for_service("payments-api")
    assert entry is not None
    assert [resource.address for resource in entry.terraform_resources] == [
        "module.payments.aws_db_instance.primary"
    ]


async def test_a_clean_run_says_nothing_in_slack(config):
    deps = build_deps(config, runner=canned_runner())
    await run(deps)
    assert deps.slack.messages == []


@pytest.fixture
def config_without_a_payments_team(config):
    """The same repositories, but nobody has declared who owns payments-api."""
    return config.model_copy(
        update={"teams": [team for team in config.teams if team.name != "payments"]}
    )


async def test_a_service_with_no_declared_team_is_persisted_unowned(
    config_without_a_payments_team,
):
    deps = build_deps(config_without_a_payments_team, runner=canned_runner())
    await run(deps)

    entry = await deps.repo.system_map_for_service("payments-api")
    assert entry is not None
    assert entry.team is None


async def test_a_service_with_no_declared_team_is_reported_to_the_platform_channel(
    config_without_a_payments_team,
):
    deps = build_deps(config_without_a_payments_team, runner=canned_runner())
    state = await run(deps)

    (message,) = deps.slack.messages
    assert message.channel == "#platform-alerts"
    assert "payments-api" in message.text
    assert state["entries_written"] == 2


async def test_a_failed_summary_does_not_stop_the_run(config):
    runner = FakeAnalysisRunner(
        results={
            AnalysisKind.SUMMARIZE_REPO: AnalysisResult.failed(
                AnalysisKind.SUMMARIZE_REPO, "clone timed out"
            ),
            AnalysisKind.SUMMARIZE_TERRAFORM: an_analysis_result(AnalysisKind.SUMMARIZE_TERRAFORM),
        }
    )
    deps = build_deps(config, runner=runner)
    state = await run(deps)

    assert state["entries_written"] == 1
    assert (SystemMapKind.TERRAFORM_MODULE, "modules/payments") in deps.repo.system_map

    (message,) = deps.slack.messages
    assert message.channel == "#platform-alerts"
    assert "clone timed out" in message.text


async def test_a_repository_config_does_not_declare_is_refused_not_summarised(config):
    runner = canned_runner()
    deps = build_deps(config, runner=runner)
    state = await run(deps, {"repos": [{"url": "github.com/org/ghost", "commit": "0000000"}]})

    assert runner.requests == []
    assert deps.repo.system_map == {}
    assert state.get("entries_written") is None
    assert "github.com/org/ghost" in deps.slack.messages[0].text


async def test_a_service_takes_its_name_from_the_summary_not_the_repository_url(config):
    runner = FakeAnalysisRunner(
        results={
            AnalysisKind.SUMMARIZE_REPO: AnalysisResult(
                kind=AnalysisKind.SUMMARIZE_REPO,
                status="succeeded",
                result=a_repo_summary(service="payments"),
            ),
            AnalysisKind.SUMMARIZE_TERRAFORM: an_analysis_result(AnalysisKind.SUMMARIZE_TERRAFORM),
        }
    )
    deps = build_deps(config, runner=runner)
    await run(deps)

    assert await deps.repo.system_map_for_service("payments") is not None
    assert await deps.repo.system_map_for_service("payments-api") is None


async def test_a_terraform_module_carries_the_resources_it_declares(config):
    deps = build_deps(config, runner=canned_runner())
    await run(deps)

    entry = deps.repo.system_map[(SystemMapKind.TERRAFORM_MODULE, "modules/payments")]
    assert entry.payload["resources"][0]["address"] == "module.payments.aws_db_instance.primary"
    assert entry.payload["mapping"]["services"] == ["payments-api"]


async def test_a_terraform_summary_with_no_modules_persists_no_module_rows(config):
    terraform = a_terraform_summary(modules={"unknown": True, "reason": "No modules/ directory."})
    runner = FakeAnalysisRunner(
        results={
            AnalysisKind.SUMMARIZE_REPO: an_analysis_result(AnalysisKind.SUMMARIZE_REPO),
            AnalysisKind.SUMMARIZE_TERRAFORM: AnalysisResult(
                kind=AnalysisKind.SUMMARIZE_TERRAFORM, status="succeeded", result=terraform
            ),
        }
    )
    deps = build_deps(config, runner=runner)
    state = await run(deps)

    assert state["entries_written"] == 1
    assert set(deps.repo.system_map) == {(SystemMapKind.SERVICE, "payments-api")}
