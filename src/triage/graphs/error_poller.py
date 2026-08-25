"""The code-exception poller, as a graph so the Platform can cron it (ADR-0025).

One tick, in two steps: read what Error Tracking says is new or regressed, then
collapse it into groups and gate them on volume. They are separate nodes because
they fail differently — the first is two API calls, the second is a rule and a
table — and because a tick that read fifteen issues and grouped none has to be
distinguishable in the trace from one that read nothing.

The hourly interval belongs to the cron that invokes it: a poller that decided
its own schedule could not be ticked by hand, and `make run-errors` is how F2
was developed without one.
"""

from langgraph.graph import END, START, StateGraph

from triage.graphs.state import ErrorPollerState
from triage.nodes.group_errors import group_error_issues
from triage.nodes.poll_errors import poll_error_issues

ErrorPollerGraph = StateGraph[ErrorPollerState, None, ErrorPollerState, ErrorPollerState]


def build_graph() -> ErrorPollerGraph:
    builder: ErrorPollerGraph = StateGraph(ErrorPollerState)
    builder.add_node("poll_error_issues", poll_error_issues)
    builder.add_node("group_error_issues", group_error_issues)
    builder.add_edge(START, "poll_error_issues")
    builder.add_edge("poll_error_issues", "group_error_issues")
    builder.add_edge("group_error_issues", END)
    return builder


graph = build_graph().compile()
