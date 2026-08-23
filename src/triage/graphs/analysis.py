"""The shared Analysis sub-graph (architecture §2.1, ADR-0005).

Takes a ranked ``Hypothesis`` list and produces one :class:`Diagnosis`. F1 calls
it after qualification; F3 will call it with one ``app`` hypothesis per slow
query. Neither knows how a hypothesis is analysed, and this graph knows nothing
about where the hypotheses came from — that separation is the reason both
features can share the expensive half of the work.

    hypotheses_in
      → select_hypotheses   (rule: floor, cap, and never nothing)
      → run_analyses        (fan-out, one branch per cause type, concurrently)
      → diagnose            (diagnosis tier: synthesise, validate, degrade)
      → END

There is no branch for a failed analysis. A hypothesis whose Job died, whose
repository is unmapped or whose commit is unknown comes back as a *stated
failure*, travels the same edge as a successful one, and lands in the diagnosis
as an unknown — because an incident where one of three analyses failed still has
two, and a graph that raises there throws both away.
"""

from langgraph.graph import END, START, StateGraph

from triage.graphs.state import AnalysisState
from triage.nodes.diagnose import diagnose
from triage.nodes.hypotheses import select_hypotheses
from triage.nodes.investigate import run_analyses

AnalysisGraph = StateGraph[AnalysisState, None, AnalysisState, AnalysisState]


def build_graph() -> AnalysisGraph:
    builder: AnalysisGraph = StateGraph(AnalysisState)

    builder.add_node("select_hypotheses", select_hypotheses)
    builder.add_node("run_analyses", run_analyses)
    builder.add_node("diagnose", diagnose)

    builder.add_edge(START, "select_hypotheses")
    builder.add_edge("select_hypotheses", "run_analyses")
    builder.add_edge("run_analyses", "diagnose")
    builder.add_edge("diagnose", END)

    return builder


graph = build_graph().compile()
