"""Terminal nodes: post the incident report, or take the Jira path config still allows."""

from langchain_core.runnables import RunnableConfig

from triage.graphs.state import TicketPipelineState
from triage.report import CONFIDENCE_LABEL, SlackReport, render_code_exception, render_incident
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.common import render as render_field
from triage.schemas.ticket import PipelineOutcome

from .persist import record_outcome


def _labels(state: TicketPipelineState) -> list[str]:
    diagnosis = state["diagnosis"]
    return [
        "triage",
        f"triage-{diagnosis.feature.value.lower()}",
        f"triage-confidence-{CONFIDENCE_LABEL[diagnosis.confidence]}",
    ]


def _channel(deps: Deps, team: str) -> str:
    return deps.config.team(team).slack_channel


async def _report(state: TicketPipelineState, deps: Deps) -> SlackReport:
    """Which report this is. The calling feature says, by what it put in the state.

    F2 hands over the group and its collection because an error group has an
    identity — a type, a place in the code, a set of tenants — that a diagnosis
    of a symptom has nowhere to put (M8 4.4). Anything else is an incident.
    """
    diagnosis = state["diagnosis"]
    workload = await deps.repo.workload_for_service(diagnosis.service)
    threshold = deps.config.confidence_threshold(diagnosis.feature)
    exception = state.get("exception")
    if exception is not None:
        return render_code_exception(
            diagnosis,
            exception.group,
            workload,
            exception.collection,
            commit=exception.commit,
            source_caveat=exception.source_caveat,
            threshold=threshold,
        )
    return render_incident(diagnosis, workload, threshold=threshold)


async def publish_report(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """The release's one terminal node: the whole diagnosis, in the team's channel.

    Every diagnosis reaches the team (ADR-0023). The confidence threshold decides
    how the report is framed, not whether it is sent, so there is nothing here to
    suppress and nothing to file.
    """
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]
    report = await _report(state, deps)
    for message in report.messages:
        await deps.slack.post(
            channel=_channel(deps, diagnosis.team),
            text=message,
            thread_ts=state.get("thread_ts"),
        )

    await record_outcome(state, deps, PipelineOutcome.REPORT_POSTED)
    return {"outcome": PipelineOutcome.REPORT_POSTED, "ticket_key": None, "ticket_url": None}


async def create_ticket(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Create the Jira issue in `Proposed by agent`, then announce it."""
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]
    draft = state["draft"]
    team = deps.config.team(diagnosis.team)

    issue = await deps.jira.create_issue(
        project=team.jira_project,
        summary=draft.summary,
        body=draft.to_markdown(),
        labels=_labels(state),
    )
    record = await deps.repo.save_ticket(
        jira_key=issue.key,
        jira_url=issue.url,
        project=team.jira_project,
        team=diagnosis.team,
        service=diagnosis.service,
        summary=draft.summary,
        diagnosis_id=state.get("diagnosis_id"),
    )

    await deps.slack.post(
        channel=team.slack_channel,
        thread_ts=state.get("thread_ts"),
        text=(
            f":memo: *{issue.key}* — {draft.summary}\n"
            f"Service `{diagnosis.service}`, confidence "
            f"*{CONFIDENCE_LABEL[diagnosis.confidence]}*. "
            f"Awaiting validation: {issue.url}"
        ),
    )

    await record_outcome(state, deps, PipelineOutcome.TICKET_CREATED, ticket_id=record.id)
    return {
        "outcome": PipelineOutcome.TICKET_CREATED,
        "ticket_key": issue.key,
        "ticket_url": issue.url,
    }


async def notify_below_threshold(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Below the confidence threshold: Slack only, no ticket.

    The notice still carries the cause and the open questions. A signal Triage
    was not confident about is exactly the one a human most needs to see.
    """
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]
    threshold = deps.config.confidence_threshold(diagnosis.feature)

    unknowns = "\n".join(
        f"• {item.question} — {item.why_unresolved}" for item in diagnosis.unknowns
    )
    text = (
        f":mag: No ticket raised for `{diagnosis.service}` — confidence "
        f"*{CONFIDENCE_LABEL[diagnosis.confidence]}*, below the "
        f"*{CONFIDENCE_LABEL[threshold]}* threshold for {diagnosis.feature.value}.\n"
        f"*Observed:* {diagnosis.symptom.description}\n"
        f"*Best guess:* {render_field(diagnosis.probable_cause)}\n"
        + (f"*Still unknown:*\n{unknowns}" if unknowns else "")
    )
    await deps.slack.post(
        channel=_channel(deps, diagnosis.team), text=text, thread_ts=state.get("thread_ts")
    )

    await record_outcome(state, deps, PipelineOutcome.BELOW_THRESHOLD)
    return {"outcome": PipelineOutcome.BELOW_THRESHOLD, "ticket_key": None, "ticket_url": None}


async def notify_review_exhausted(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Retry budget spent: hand the draft to a human rather than filing it.

    Filing a ticket the reviewer just rejected would put the burden back on the
    developer, which is the entire thing Triage exists to remove.
    """
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]
    draft = state["draft"]
    verdict = state["verdict"]

    failing = ", ".join(section.value for section in verdict.missing) or "unspecified"
    text = (
        f":warning: Draft ticket for `{diagnosis.service}` failed self-review "
        f"{state.get('compose_attempts', 0)} times; not filed.\n"
        f"*Unresolved sections:* {failing}\n"
        f"*Reviewer feedback:* {verdict.feedback}\n"
        f"The draft is attached — it needs a human before it goes to the team."
    )
    await deps.slack.post(
        channel=_channel(deps, diagnosis.team),
        text=text,
        attachment=f"# {draft.summary}\n\n{draft.to_markdown()}",
        thread_ts=state.get("thread_ts"),
    )

    await record_outcome(state, deps, PipelineOutcome.REVIEW_EXHAUSTED)
    return {"outcome": PipelineOutcome.REVIEW_EXHAUSTED, "ticket_key": None, "ticket_url": None}
