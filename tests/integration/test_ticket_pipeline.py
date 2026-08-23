"""End-to-end runs of the ticket pipeline over every branch.

Assertions are on what the pipeline *did* — the Jira issues created, the Slack
messages posted, the evaluation rows written — not on model prose, which is
canned here. Prompt quality is measured by ``evals/``, not by these tests.
"""

import pytest

from tests.conftest import a_draft, a_verdict, build_deps, no_match, run_config
from triage.db.repo import InMemoryRepository
from triage.graphs.ticket_pipeline import graph
from triage.schemas import DedupDecision, PipelineOutcome, TicketDraft, TicketSection


async def run(diagnosis, deps):
    return await graph.ainvoke({"diagnosis": diagnosis}, config=run_config(deps))


async def test_high_confidence_diagnosis_becomes_a_ticket(config, oom_diagnosis):
    deps = build_deps(config)
    state = await run(oom_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.TICKET_CREATED
    assert len(deps.jira.created) == 1
    assert deps.jira.created[0].project == "PAY"
    assert state["ticket_key"] == "PAY-1"

    body = deps.jira.created[0].body
    for section in TicketSection:
        assert f"## {section.heading}" in body

    assert deps.slack.messages[0].channel == "#payments-alerts"
    assert "PAY-1" in deps.slack.messages[0].text


async def test_created_ticket_is_persisted_for_future_dedup(config, oom_diagnosis):
    deps = build_deps(config)
    await run(oom_diagnosis, deps)

    open_tickets = await deps.repo.open_tickets_for_service("payments-api")
    assert [t.jira_key for t in open_tickets] == ["PAY-1"]


async def test_ticket_is_labelled_with_feature_and_confidence(config, oom_diagnosis):
    deps = build_deps(config)
    await run(oom_diagnosis, deps)
    assert set(deps.jira.created[0].labels) == {"triage", "triage-f1", "triage-confidence-high"}


async def test_low_confidence_produces_a_notice_and_no_ticket(config, low_confidence_diagnosis):
    deps = build_deps(config)
    state = await run(low_confidence_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.BELOW_THRESHOLD
    assert deps.jira.created == []
    assert state["ticket_key"] is None

    (message,) = deps.slack.messages
    assert "No ticket raised" in message.text
    # The notice must still carry what was learned, including what was not.
    assert "p95" in message.text
    assert "node hosting the pods" in message.text


async def test_below_threshold_never_calls_the_composer(config, low_confidence_diagnosis):
    """The gate is there to save the expensive tiers, not just to suppress a ticket."""
    deps = build_deps(config)
    await run(low_confidence_diagnosis, deps)
    assert deps.llm.calls_for(TicketDraft) == []


async def test_review_failure_retries_with_feedback_then_succeeds(config, oom_diagnosis):
    deps = build_deps(
        config,
        verdicts=[a_verdict(False, "Location is missing the commit."), a_verdict(True)],
        drafts=[a_draft(location="Repo only."), a_draft()],
    )
    state = await run(oom_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.TICKET_CREATED
    assert state["compose_attempts"] == 2
    # The retry must see the reviewer's words, or it just repeats itself.
    retry_prompt = deps.llm.calls_for(TicketDraft)[1].prompt
    assert "Location is missing the commit." in retry_prompt
    assert "<previous_draft>" in retry_prompt


async def test_first_compose_is_not_polluted_by_feedback(config, oom_diagnosis):
    deps = build_deps(config)
    await run(oom_diagnosis, deps)
    assert "<reviewer_feedback>" not in deps.llm.calls_for(TicketDraft)[0].prompt


async def test_exhausted_review_hands_the_draft_to_a_human(config, oom_diagnosis):
    """Three failures must not file the ticket anyway; that would restore the burden."""
    deps = build_deps(config, verdicts=[a_verdict(False, "Cause is not supported by evidence.")])
    state = await run(oom_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.REVIEW_EXHAUSTED
    assert deps.jira.created == []
    assert len(deps.llm.calls_for(TicketDraft)) == config.thresholds.max_compose_attempts

    (message,) = deps.slack.messages
    assert "failed self-review" in message.text
    assert message.attachment is not None
    assert "## Symptom" in message.attachment


async def test_matched_duplicate_updates_instead_of_creating(config, oom_diagnosis):
    repo = InMemoryRepository()
    await repo.save_ticket(
        jira_key="PAY-7",
        jira_url="https://jira.invalid/browse/PAY-7",
        project="PAY",
        team="payments",
        service="payments-api",
        summary="payments-api OOM during settlement",
        diagnosis_id=None,
    )
    deps = build_deps(
        config,
        repo=repo,
        dedup=[DedupDecision(matched=True, ticket_key="PAY-7", reasoning="Same unbounded cache.")],
    )
    state = await run(oom_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.TICKET_UPDATED
    assert state["ticket_key"] == "PAY-7"
    assert deps.jira.created == []
    assert deps.jira.comments[0].key == "PAY-7"
    assert "Recurrence #2" in deps.jira.comments[0].body
    assert (await repo.get_ticket("PAY-7")).occurrence_count == 2


async def test_quiet_recurrence_is_still_announced(config, oom_diagnosis):
    """A wrong dedup match must be visible, so every match posts something."""
    repo = InMemoryRepository()
    await repo.save_ticket(
        jira_key="PAY-7",
        jira_url="https://jira.invalid/browse/PAY-7",
        project="PAY",
        team="payments",
        service="payments-api",
        summary="payments-api OOM during settlement",
        diagnosis_id=None,
    )
    deps = build_deps(
        config,
        repo=repo,
        dedup=[DedupDecision(matched=True, ticket_key="PAY-7", reasoning="Same cause.")],
    )
    await run(oom_diagnosis, deps)

    (message,) = deps.slack.messages
    assert "Recurrence #2" in message.text
    assert "has now recurred" not in message.text  # not yet escalated


async def test_third_recurrence_escalates(config, oom_diagnosis):
    repo = InMemoryRepository()
    await repo.save_ticket(
        jira_key="PAY-7",
        jira_url="https://jira.invalid/browse/PAY-7",
        project="PAY",
        team="payments",
        service="payments-api",
        summary="payments-api OOM during settlement",
        diagnosis_id=None,
    )
    await repo.bump_occurrence("PAY-7")  # now at 2; this run makes it 3
    deps = build_deps(
        config,
        repo=repo,
        dedup=[DedupDecision(matched=True, ticket_key="PAY-7", reasoning="Same cause.")],
    )
    await run(oom_diagnosis, deps)

    (message,) = deps.slack.messages
    assert "has now recurred 3 times" in message.text
    ticket = await repo.get_ticket("PAY-7")
    assert ticket.last_alerted_occurrence == 3


async def test_hallucinated_dedup_key_is_discarded(config, oom_diagnosis):
    """Appending evidence to a ticket that was never offered would bury an incident."""
    repo = InMemoryRepository()
    await repo.save_ticket(
        jira_key="PAY-7",
        jira_url="https://jira.invalid/browse/PAY-7",
        project="PAY",
        team="payments",
        service="payments-api",
        summary="An unrelated open ticket",
        diagnosis_id=None,
    )
    deps = build_deps(
        config,
        repo=repo,
        dedup=[DedupDecision(matched=True, ticket_key="PAY-999", reasoning="Looks similar.")],
    )
    state = await run(oom_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.TICKET_CREATED
    assert deps.jira.comments == []
    assert "Discarded match" in state["dedup"].reasoning


async def test_dedup_skips_the_model_when_there_is_nothing_to_compare(config, oom_diagnosis):
    deps = build_deps(config)
    await run(oom_diagnosis, deps)
    assert deps.llm.calls_for(DedupDecision) == []
    assert deps.llm.calls_for(TicketDraft)  # but the rest of the pipeline ran


async def test_each_node_uses_its_intended_model_tier(config, oom_diagnosis):
    """Routing by tier is a cost decision; a node drifting to Opus is a silent bill."""
    repo = InMemoryRepository()
    await repo.save_ticket(
        jira_key="PAY-7",
        jira_url="https://jira.invalid/browse/PAY-7",
        project="PAY",
        team="payments",
        service="payments-api",
        summary="Unrelated open ticket",
        diagnosis_id=None,
    )
    deps = build_deps(config, repo=repo, dedup=[no_match()])
    await run(oom_diagnosis, deps)

    tiers = {call.schema.__name__: call.tier for call in deps.llm.calls}
    assert tiers == {
        "DedupDecision": "triage",
        "TicketDraft": "analysis",
        "ReviewVerdict": "diagnosis",
    }


@pytest.mark.parametrize(
    ("fixture_name", "expected_outcome", "verdicts"),
    [
        ("oom_diagnosis", PipelineOutcome.TICKET_CREATED, None),
        ("low_confidence_diagnosis", PipelineOutcome.BELOW_THRESHOLD, None),
        ("oom_diagnosis", PipelineOutcome.REVIEW_EXHAUSTED, [a_verdict(False, "Unsupported.")]),
    ],
)
async def test_every_terminal_path_records_an_evaluation(
    config, request, fixture_name, expected_outcome, verdicts
):
    """A metric that only records successes measures nothing."""
    diagnosis = request.getfixturevalue(fixture_name)
    deps = build_deps(config, verdicts=verdicts)
    await run(diagnosis, deps)

    (evaluation,) = deps.repo.evaluations
    assert evaluation.outcome is expected_outcome
    assert evaluation.feature is diagnosis.feature
    assert evaluation.diagnosis_id == diagnosis.diagnosis_id


async def test_time_to_ticket_is_recorded_only_when_a_ticket_exists(
    config, oom_diagnosis, low_confidence_diagnosis
):
    ticketed = build_deps(config)
    await run(oom_diagnosis, ticketed)
    assert ticketed.repo.evaluations[0].time_to_ticket_seconds is not None

    not_ticketed = build_deps(config)
    await run(low_confidence_diagnosis, not_ticketed)
    assert not_ticketed.repo.evaluations[0].time_to_ticket_seconds is None


async def test_diagnosis_is_stored_before_anything_can_fail(config, oom_diagnosis):
    deps = build_deps(config)
    await run(oom_diagnosis, deps)
    assert deps.repo.diagnoses[oom_diagnosis.diagnosis_id] == oom_diagnosis
