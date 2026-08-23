"""The service-mapping graph: which repository each running service is (M6).

    services | nothing
      → select_services → derive_workloads → persist_workloads → END

Linear, because every branch it could have would be a service-level decision and
those belong in the derivation, which produces one outcome per service rather
than routing the whole pass on the first one. Nothing here raises on a service it
cannot map: a pass that mapped nine out of ten is worth persisting, and the tenth
is a line in the result.
"""

from langgraph.graph import END, START, StateGraph

from triage.graphs.state import MappingState
from triage.nodes.mapping import derive_workloads, persist_workloads, select_services

MappingGraph = StateGraph[MappingState, None, MappingState, MappingState]


def build_graph() -> MappingGraph:
    builder: MappingGraph = StateGraph(MappingState)

    builder.add_node("select_services", select_services)
    builder.add_node("derive_workloads", derive_workloads)
    builder.add_node("persist_workloads", persist_workloads)

    builder.add_edge(START, "select_services")
    builder.add_edge("select_services", "derive_workloads")
    builder.add_edge("derive_workloads", "persist_workloads")
    builder.add_edge("persist_workloads", END)

    return builder


graph = build_graph().compile()
