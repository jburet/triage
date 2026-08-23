"""The alert poller, as a graph so the Platform can cron it (ADR-0017).

One node. It is a graph rather than a script because that is how the Platform
schedules work — the same place the cartography full pass and F3's daily tick
will live — and because a tick that dies then leaves a trace in the same viewer
as everything else.

The 60-second interval is not in here: it belongs to the cron that invokes it,
and a poller that decided its own schedule could not be run by hand.
"""

from langgraph.graph import END, START, StateGraph

from triage.graphs.state import PollerState
from triage.nodes.poll import poll_alerts

PollerGraph = StateGraph[PollerState, None, PollerState, PollerState]


def build_graph() -> PollerGraph:
    builder: PollerGraph = StateGraph(PollerState)
    builder.add_node("poll_alerts", poll_alerts)
    builder.add_edge(START, "poll_alerts")
    builder.add_edge("poll_alerts", END)
    return builder


graph = build_graph().compile()
