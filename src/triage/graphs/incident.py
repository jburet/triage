"""F1 — the incident graph (architecture §2.3, roadmap F1).

One alert, already known to have persisted (ADR-0018) and already owned
(ADR-0017), becomes a developable ticket and a post-mortem draft.

    alert_in
      → open_incident      (signal → analysing; the Slack thread everything replies into)
      → classify_alert     (triage tier: the class, and nothing else)
      → collect            (the class's sweep, concurrently)
      → follow_up ⟲        (analysis tier, up to `collection.max_followup_calls`)
      → qualify            (analysis tier: ranked causes; commits come from the map)
      → [analysis]         (shared sub-graph: fan-out, then a Diagnosis)
      → [ticket_pipeline]  (shared sub-graph: dedup, gate, compose, review, Jira)
      → draft_postmortem   (only when a ticket exists — ADR-0010)
      → settle_signal      (ticketed or discarded, on the signal itself)

The two shared sub-graphs are composed rather than re-implemented, which is the
whole reason they were built standalone: F1 adds collection at the front and a
write-up at the back, and owns neither the diagnosis nor the ticket.

Nothing in this module decides *whether* to run. That was settled before the
graph was invoked — by the gate, and by the scope resolution that found an owner.
"""

from typing import cast

from langgraph.graph import END, START, StateGraph

from triage.graphs.analysis import build_graph as build_analysis
from triage.graphs.state import IncidentState
from triage.graphs.ticket_pipeline import build_graph as build_ticket_pipeline
from triage.nodes.collect import classify_alert, collect, follow_up, route_after_follow_up
from triage.nodes.incident import (
    draft_postmortem,
    open_incident,
    route_after_pipeline,
    settle_signal,
)
from triage.nodes.qualify import qualify
from triage.runtime import DEPS_KEY, Deps
from triage.schemas.signal import SignalStatus

IncidentGraph = StateGraph[IncidentState, None, IncidentState, IncidentState]


def build_graph() -> IncidentGraph:
    builder: IncidentGraph = StateGraph(IncidentState)

    builder.add_node("open_incident", open_incident)
    builder.add_node("classify_alert", classify_alert)
    builder.add_node("collect", collect)
    builder.add_node("follow_up", follow_up)
    builder.add_node("qualify", qualify)
    builder.add_node("analysis", build_analysis().compile())
    builder.add_node("ticket_pipeline", build_ticket_pipeline().compile())
    builder.add_node("draft_postmortem", draft_postmortem)
    builder.add_node("settle_signal", settle_signal)

    builder.add_edge(START, "open_incident")
    builder.add_edge("open_incident", "classify_alert")
    builder.add_edge("classify_alert", "collect")
    builder.add_edge("collect", "follow_up")
    builder.add_conditional_edges("follow_up", route_after_follow_up)
    builder.add_edge("qualify", "analysis")
    builder.add_edge("analysis", "ticket_pipeline")
    builder.add_conditional_edges("ticket_pipeline", route_after_pipeline)
    builder.add_edge("draft_postmortem", "settle_signal")
    builder.add_edge("settle_signal", END)

    return builder


graph = build_graph().compile()


async def run_incident(
    state: IncidentState, deps: Deps, *, thread_id: str | None = None
) -> IncidentState:
    """Invoke the graph in this process, recording a crash on the signal (behaviour 3.5).

    A run that dies leaves a signal that would otherwise sit in ``analysing``
    forever, and the poller would never look at that cycle again. The failure is
    written down and re-raised: swallowing it would make a broken Triage look
    like a quiet one.
    """
    configurable: dict[str, object] = {DEPS_KEY: deps}
    if thread_id is not None:
        configurable["thread_id"] = thread_id
    try:
        return cast(
            IncidentState, await graph.ainvoke(state, config={"configurable": configurable})
        )
    except Exception:
        signal = state.get("signal")
        if signal is not None:
            await deps.repo.update_signal(signal.model_copy(update={"status": SignalStatus.FAILED}))
        raise
