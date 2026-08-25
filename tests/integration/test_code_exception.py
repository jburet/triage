"""F2 end to end: one gated group becomes one threaded report (M8 4.1-4.6).

Offline. Datadog is the recording fake and answers the way the real org does —
nothing, for a stated reason (ADR-0029) — the model is `FakeLLM`, and the
analysis runner is canned. What is worth pinning here is not that a model was
called: it is where the analysis was pointed, which commit it read, that the diff
that cannot run says so, and that the group's row and its Slack thread survive
the run.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import (
    a_qualification,
    a_workload,
    build_deps,
    declaring,
    run_config,
)
from triage.analysis.runner import FakeAnalysisRunner
from triage.db.repo import InMemoryRepository
from triage.graphs.code_exception import graph, run_code_exception
from triage.integrations.datadog import FakeDatadogClient
from triage.integrations.github import FakeGitHubClient
from triage.report import EXCEPTION_HEADING
from triage.runtime import Deps
from triage.schemas.analysis import AnalysisKind
from triage.schemas.errors import ErrorGroup, ErrorGroupStatus, ErrorTrack, Novelty
from triage.schemas.ticket import PipelineOutcome

NOW = datetime(2026, 8, 25, 5, 35, tzinfo=UTC)
REPO = "github.com/org/payments-api"
AN_APP_CAUSE = {
    "cause_type": "app",
    "service": "payments-us",
    "description": "The lookup returns None and the caller treats it as fatal.",
    "rank_score": 0.8,
}


def a_group(**overrides: object) -> ErrorGroup:
    base: dict[str, object] = {
        "key": "EntityNotFoundException|OdbClient.scala|$anonfun$load$6|payments-api",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "repository": "payments-api",
        "repo_url": REPO,
        "team": "payments",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"payments-eu": 40, "payments-us": 5869},
        "occurrences": 5909,
        "cumulative_occurrences": 5909,
        "issue_ids": ["1e4f8c2a"],
        "sample_message": "Entity not found: load_contact_by_id",
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
        "status": ErrorGroupStatus.ANALYSING,
        "analysis_count": 1,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


async def a_repository() -> InMemoryRepository:
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-us",
            repository="payments-api",
            repo_url=REPO,
            deployed_commit="cafe123",
        )
    )
    return repo


async def deps_for(**over: object) -> Deps:
    return build_deps(
        declaring(REPO, team="payments"),
        repo=over.pop("repo", None) or await a_repository(),
        datadog=over.pop("datadog", None) or FakeDatadogClient(),
        github=over.pop("github", None) or FakeGitHubClient(),
        runner=over.pop("runner", None),
        qualifications=over.pop("qualifications", None) or [a_qualification(AN_APP_CAUSE)],
    )


async def run(deps: Deps, group: ErrorGroup | None = None) -> dict:
    return await graph.ainvoke({"group": group or a_group()}, config=run_config(deps))


async def test_a_gated_group_becomes_one_report_and_settles_on_its_own_row():
    deps = await deps_for()

    state = await run(deps)

    assert state["outcome"] is PipelineOutcome.REPORT_POSTED
    assert state["group"].status is ErrorGroupStatus.REPORTED
    assert deps.jira.created == []
    posted = "\n".join(message.text for message in deps.slack.messages)
    assert EXCEPTION_HEADING in posted
    assert "zeenea.commons.exceptions.EntityNotFoundException" in posted
    assert "`payments-us` 5,869" in posted


async def test_the_code_analysis_is_pointed_at_the_class_the_issue_named():
    """M8 4.1 — the fix for what M7 3.3 measured."""
    runner = FakeAnalysisRunner(default=lambda request: _succeeded(request))
    deps = await deps_for(runner=runner)

    await run(deps)

    code = next(r for r in runner.requests if r.kind is AnalysisKind.CODE_ANALYSIS)
    assert code.paths == [
        "src/main/scala/zeenea/repository/orientdb/OdbClient.scala",
        "zeenea/repository/orientdb/OdbClient.scala",
    ]


async def test_a_claimed_version_is_the_commit_read_and_the_report_says_so():
    deps = await deps_for(github=FakeGitHubClient(tags={(REPO, "501"): "deadbee"}))

    await run(deps, a_group(first_seen_version="501", last_seen_version="514"))

    posted = "\n".join(message.text for message in deps.slack.messages)
    assert "*Commit:* deadbee" in posted
    assert "first seen on version `501`" in posted


async def test_with_nothing_claiming_the_version_the_report_says_it_fell_back():
    deps = await deps_for()

    await run(deps)

    posted = "\n".join(message.text for message in deps.slack.messages)
    assert "*Commit:* cafe123" in posted
    assert "nothing claims the version" in posted


async def test_the_diff_that_cannot_run_lands_as_an_unknown_and_not_as_silence():
    """M8 4.3, ADR-0014: `diff_analysis` has no base commit and no entrypoint."""
    deps = await deps_for(github=FakeGitHubClient(tags={(REPO, "501"): "deadbee"}))

    state = await run(deps, a_group(first_seen_version="501", last_seen_version="514"))

    unknowns = [item.why_unresolved for item in state["diagnosis"].unknowns]
    assert any("no earlier commit is known" in why for why in unknowns)
    questions = " ".join(item.question for item in state["diagnosis"].unknowns)
    assert "`501`" in questions
    assert "`514`" in questions


async def test_the_absence_datadog_discards_reaches_the_report():
    deps = await deps_for()

    await run(deps)

    posted = "\n".join(message.text for message in deps.slack.messages)
    assert "What was searched for and not found:" in posted


async def test_every_message_about_one_group_is_in_one_thread_across_ticks():
    """M8 4.5 — the fourth report replies under the first, not beside it."""
    repo = await a_repository()
    first = await deps_for(repo=repo)
    await run(first)
    thread = (await repo.error_group(a_group().key)).thread_ts
    assert thread is not None

    second = await deps_for(repo=repo)
    await run(second, a_group(analysis_count=2, thread_ts=thread))

    assert [message.thread_ts for message in second.slack.messages] == [thread] * len(
        second.slack.messages
    )
    assert "This is report 2" in second.slack.messages[0].text


async def test_a_run_that_dies_leaves_the_group_open_rather_than_analysing():
    """M8 4.6 — as `run_incident` does for a signal, but recoverable rather than failed."""
    repo = await a_repository()
    deps = await deps_for(repo=repo, runner=FakeAnalysisRunner(results={}))

    with pytest.raises(AssertionError):
        await run_code_exception({"group": a_group()}, deps)

    assert (await repo.error_group(a_group().key)).status is ErrorGroupStatus.OPEN


def _succeeded(request):
    from tests.conftest import an_analysis_result

    return an_analysis_result(request.kind)
