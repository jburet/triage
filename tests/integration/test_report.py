"""The Slack-only release: what a diagnosis becomes when nothing writes to Jira.

Assertions are on what the pipeline did — the messages posted, the Jira fake
that must have recorded nothing — and on what the rendered report says, because
under ADR-0023 the report *is* the product and there is no ticket behind it to
carry the content instead.
"""

from tests.conftest import (
    a_service_entry,
    a_verdict,
    build_deps,
    fake_datadog,
    mapped,
    pod_down_alert,
    run_config,
)
from triage.graphs.incident import build_graph
from triage.graphs.ticket_pipeline import graph
from triage.schemas import PipelineOutcome, ReviewVerdict, TicketDraft
from triage.schemas.signal import SignalStatus


async def run(diagnosis, deps):
    return await graph.ainvoke({"diagnosis": diagnosis}, config=run_config(deps))


async def test_a_confident_diagnosis_is_reported_and_never_filed(config, oom_diagnosis):
    """The release writes only to Slack: the recording fake must record nothing."""
    deps = build_deps(config)
    state = await run(oom_diagnosis, deps)

    assert state["outcome"] is PipelineOutcome.REPORT_POSTED
    assert deps.jira.created == []
    assert deps.jira.comments == []
    assert state["ticket_key"] is None
    assert deps.slack.messages


async def test_the_report_costs_no_model_call(config, oom_diagnosis):
    """The renderer is a rule: nothing about the report is worth a tier call."""
    deps = build_deps(config)
    await run(oom_diagnosis, deps)
    assert deps.llm.calls_for(TicketDraft) == []


async def test_a_reported_incident_settles_as_reported_rather_than_discarded(config):
    """F1 end to end under the release: nothing filed, and the signal says so.

    ``discarded`` is what the signal said before this: the outcome table only knew
    about tickets, so an incident whose whole report went to the team read, in
    the database Triage evaluates itself from, as one it had thrown away.
    """
    deps = build_deps(
        config,
        repo=mapped(a_service_entry("plt-hcl-software-uat")),
        datadog=fake_datadog(),
    )
    result = (
        await build_graph()
        .compile()
        .ainvoke({"alert": pod_down_alert(), "team": "platform"}, config=run_config(deps))
    )

    assert result["outcome"] is PipelineOutcome.REPORT_POSTED
    assert deps.jira.created == []
    assert deps.jira.comments == []
    assert result["signal"].status is SignalStatus.REPORTED


async def test_one_line_of_yaml_puts_the_ticket_path_back(config, jira_config, oom_diagnosis):
    """The same diagnosis, the same code, two destinations — decided by config alone.

    ADR-0023 postpones Jira; it does not remove it. The composer, the
    self-review and the client have to stay reachable, or "reversible by
    configuration" is a claim nothing checks.
    """
    reported = build_deps(config)
    filed = build_deps(jira_config)

    release = await run(oom_diagnosis, reported)
    reversed_ = await run(oom_diagnosis, filed)

    assert release["outcome"] is PipelineOutcome.REPORT_POSTED
    assert reported.jira.created == []
    assert reversed_["outcome"] is PipelineOutcome.TICKET_CREATED
    assert filed.jira.created[0].project == "PAY"


async def test_the_threshold_frames_the_report_instead_of_routing_it(
    config, oom_diagnosis, low_confidence_diagnosis
):
    """Both diagnoses reach the same channel; only the first line differs (ADR-0023).

    Above the bar the reader is told the cause, because that is what they act
    on. Below it, leading with a cause Triage cannot stand behind is the one
    thing the report may not do — so it leads with what is established and says
    how much is still open.
    """
    confident = build_deps(config)
    unsure = build_deps(config)
    await run(oom_diagnosis, confident)
    await run(low_confidence_diagnosis, unsure)

    (loud,) = confident.slack.messages
    (quiet,) = unsure.slack.messages
    assert loud.channel == quiet.channel == "#payments-alerts"

    lead, follow = loud.text.split("\n")[:2]
    assert "unbounded" in lead.lower()
    assert "at or above the *medium*" in follow

    lead, follow = quiet.text.split("\n")[:2]
    assert "p95 latency on GET /payments/{id} rose from 140 ms to 610 ms" in lead
    assert "Latency rose across every handler" not in lead
    assert "below the *medium*" in follow
    assert "2 questions" in follow


async def test_a_draft_that_could_never_pass_review_is_still_reported(
    config, jira_config, oom_diagnosis
):
    """There is no filing decision left to exhaust, so nothing is handed back.

    The retry budget exists to stop Triage filing a ticket its own reviewer
    rejected. Nothing is being filed, so the same run that used to end as
    "failed self-review three times; not filed" now ends as the report, and the
    two expensive tiers it burned getting there are not spent at all.
    """
    always_fails = [a_verdict(False, "Cause is not supported by evidence.")]
    reported = build_deps(config, verdicts=always_fails)
    exhausted = build_deps(jira_config, verdicts=always_fails)

    release = await run(oom_diagnosis, reported)
    old = await run(oom_diagnosis, exhausted)

    assert old["outcome"] is PipelineOutcome.REVIEW_EXHAUSTED
    assert release["outcome"] is PipelineOutcome.REPORT_POSTED
    assert reported.llm.calls_for(TicketDraft) == []
    assert reported.llm.calls_for(ReviewVerdict) == []

    (message,) = reported.slack.messages
    assert "failed self-review" not in message.text
    assert oom_diagnosis.symptom.description in message.text
    assert oom_diagnosis.probable_cause in message.text
