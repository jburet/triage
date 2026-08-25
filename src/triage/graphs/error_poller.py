"""The code-exception poller, as a graph so the Platform can cron it (ADR-0025).

One node, for the same reasons the alert poller is one: the Platform schedules
graphs, and a tick that dies leaves its trace in the same viewer as everything
else. The hourly interval belongs to the cron that invokes it — a poller that
decided its own schedule could not be ticked by hand, and `make run-errors` is
how F2 was developed without one.
"""

from langgraph.graph import END, START, StateGraph

from triage.graphs.state import ErrorPollerState
from triage.nodes.poll_errors import poll_error_issues

ErrorPollerGraph = StateGraph[ErrorPollerState, None, ErrorPollerState, ErrorPollerState]


def build_graph() -> ErrorPollerGraph:
    builder: ErrorPollerGraph = StateGraph(ErrorPollerState)
    builder.add_node("poll_error_issues", poll_error_issues)
    builder.add_edge(START, "poll_error_issues")
    builder.add_edge("poll_error_issues", END)
    return builder


graph = build_graph().compile()
