"""The Slack-only release: what a diagnosis becomes when nothing writes to Jira.

Assertions are on what the pipeline did — the messages posted, the Jira fake
that must have recorded nothing — and on what the rendered report says, because
under ADR-0023 the report *is* the product and there is no ticket behind it to
carry the content instead.
"""

from tests.conftest import (
    a_service_entry,
    build_deps,
    fake_datadog,
    mapped,
    pod_down_alert,
    run_config,
)
from triage.graphs.incident import build_graph
from triage.graphs.ticket_pipeline import graph
from triage.schemas import PipelineOutcome, TicketDraft
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
