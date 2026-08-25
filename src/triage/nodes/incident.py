"""The F1-specific nodes: opening an incident, settling it, and writing it up.

The Slack notice goes out *before* any analysis, and it is the thread everything
else replies into. It is not a courtesy: by the time this graph runs the alert has
been firing for the persistence gate (ADR-0018), and the team's first question is
whether anyone is looking. One line saying Triage is, with how long it has been
firing, answers that at the cost of one API call — and the notices that follow
land under it instead of scattering through the channel.

The post-mortem is a Jira comment on the ticket, and Slack gets the link
(ADR-0010). It is drafted only when a ticket exists: a post-mortem for an
incident nobody ticketed has no home, and a Slack message with a whole
post-mortem pasted into it pushes the incident thread out of view.
"""

from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from langchain_core.runnables import RunnableConfig

from triage.graphs.state import IncidentState
from triage.nodes.collect import alert_payload
from triage.prompts import render
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.alert import Alert
from triage.schemas.collection import Collection
from triage.schemas.common import Feature
from triage.schemas.postmortem import Postmortem
from triage.schemas.signal import Signal, SignalStatus
from triage.schemas.ticket import PipelineOutcome

log = structlog.get_logger(__name__)

SETTLED_STATUS: dict[PipelineOutcome, SignalStatus] = {
    PipelineOutcome.TICKET_CREATED: SignalStatus.TICKETED,
    PipelineOutcome.TICKET_UPDATED: SignalStatus.TICKETED,
    PipelineOutcome.REPORT_POSTED: SignalStatus.REPORTED,
}
"""How an incident ended, as the signal records it. Anything absent is a run that
concluded nothing a human was shown, and is ``discarded``."""


def _signal_for(alert: Alert, team: str | None, service: str) -> Signal:
    """A signal for an alert that arrived without one — Studio, evals, a manual run."""
    return Signal(
        feature=Feature.F1,
        source="datadog",
        external_id=alert.event_id,
        service=service,
        team=team,
        monitor_id=alert.monitor_id,
        group=alert.group,
        cycle_key=alert.cycle_key,
        fired_at=alert.fired_at,
        status=SignalStatus.ANALYSING,
        payload=alert.raw,
    )


def _firing_for(alert: Alert, now: datetime | None = None) -> str:
    minutes = int(((now or datetime.now(UTC)) - alert.fired_at).total_seconds() // 60)
    return f"{minutes} minutes" if minutes >= 1 else "under a minute"


async def open_incident(
    state: IncidentState, config: RunnableConfig | None = None
) -> IncidentState:
    """Mark the signal as being analysed and open the Slack thread for it."""
    deps = deps_from_runnable_config(config)
    alert = state["alert"]
    team = state.get("team") or _fallback_team(deps, alert)
    service = state.get("service") or alert.scope.workload or "unknown"
    signal = state.get("signal") or _signal_for(alert, team, service)
    signal = await deps.repo.save_signal(
        signal.model_copy(update={"status": SignalStatus.ANALYSING})
    )

    thread_ts = await deps.slack.post(
        channel=deps.config.team(team).slack_channel,
        text=(
            f":mag: Investigating `{service}` — *{alert.monitor_name or 'alert'}* has been "
            f"firing for {_firing_for(alert)}"
            + (f" ({alert.group})" if alert.group else "")
            + ".\nTriage is collecting telemetry and will report here."
            + (
                f"\n<https://app.datadoghq.eu{alert.alert_url}|Monitor status>"
                if alert.alert_url
                else ""
            )
        ),
    )
    return {
        "signal": signal,
        "signal_id": signal.signal_id,
        "feature": Feature.F1,
        "team": team,
        "service": service,
        "thread_ts": thread_ts,
    }


def _fallback_team(deps: Deps, alert: Alert) -> str:
    from triage.config import PLATFORM_TEAM
    from triage.scope import resolve

    routing = resolve(deps.config, alert)
    return routing.team or PLATFORM_TEAM


async def settle_signal(
    state: IncidentState, config: RunnableConfig | None = None
) -> IncidentState:
    """Record how the incident ended, on the signal itself (behaviour 3.5)."""
    deps = deps_from_runnable_config(config)
    signal = state.get("signal")
    if signal is None:
        return {}
    outcome = state.get("outcome")
    status = SignalStatus.DISCARDED
    if outcome is not None:
        status = SETTLED_STATUS.get(outcome, status)
    return {"signal": await deps.repo.update_signal(signal.model_copy(update={"status": status}))}


def route_after_pipeline(state: IncidentState) -> Literal["draft_postmortem", "settle_signal"]:
    return "draft_postmortem" if state.get("ticket_key") else "settle_signal"


def _timeline_source(collection: Collection | None) -> list[dict[str, Any]]:
    """The events, in time order — the raw material for a timeline, not a narrative."""
    if collection is None:
        return []
    events: list[dict[str, Any]] = []
    for result in collection.results:
        events.extend(result.payload.get("events", []) or [])
    return sorted(events, key=lambda event: str(event.get("at") or ""))


async def draft_postmortem(
    state: IncidentState, config: RunnableConfig | None = None
) -> IncidentState:
    """Write the draft, comment it on the ticket, link it from the thread (ADR-0010)."""
    deps = deps_from_runnable_config(config)
    diagnosis = state["diagnosis"]
    ticket_key = state["ticket_key"]
    assert ticket_key is not None  # the router only sends a ticketed incident here

    postmortem = await deps.llm.call(
        "analysis",
        render(
            "postmortem",
            alert=alert_payload(state["alert"]),
            diagnosis=diagnosis.model_dump(mode="json"),
            events=_timeline_source(state.get("collection")),
        ),
        Postmortem,
    )
    await deps.jira.add_comment(key=ticket_key, body=postmortem.to_markdown())
    await deps.slack.post(
        channel=deps.config.team(diagnosis.team).slack_channel,
        thread_ts=state.get("thread_ts"),
        text=(
            f":notebook: Post-mortem draft added as a comment on *{ticket_key}*: "
            f"{state.get('ticket_url') or ticket_key}"
        ),
    )
    return {"postmortem": postmortem.to_markdown()}
