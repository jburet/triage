"""The F0 cartography graph (architecture §2.5, roadmap F0).

Builds the map every other feature locates code, infrastructure and owners
through. It runs from two triggers: a scheduled pass over the repositories
``config.yaml`` declares, and a merge to ``main`` naming one repository and one
commit (ADR-0006).

    repos | merge_event
      → select_targets
          ├─ nothing to do → END (or notify_platform, if a repository was refused)
          └─ → summarize → build_system_map → persist_map
                              ├─ gaps → notify_platform → END
                              └─ clean → END

Nothing here raises on a repository it cannot read: a run that summarised four
repositories out of five is worth persisting, and the fifth is a Slack notice to
the team that owns the configuration.
"""

from typing import Literal, cast

from langgraph.graph import END, START, StateGraph

from triage.graphs.state import CartographyState
from triage.nodes.summarize import select_targets, summarize
from triage.nodes.system_map import build_system_map, notify_platform, persist_map


def route_after_select(
    state: CartographyState,
) -> Literal["summarize", "notify_platform", "__end__"]:
    """A run with no target still reports, when the reason is a repository it refused."""
    if state.get("targets"):
        return "summarize"
    if state.get("failures"):
        return "notify_platform"
    return cast(Literal["__end__"], END)


def route_after_persist(state: CartographyState) -> Literal["notify_platform", "__end__"]:
    if state.get("unowned") or state.get("failures"):
        return "notify_platform"
    return cast(Literal["__end__"], END)


CartographyGraph = StateGraph[CartographyState, None, CartographyState, CartographyState]


def build_graph() -> CartographyGraph:
    builder: CartographyGraph = StateGraph(CartographyState)

    builder.add_node("select_targets", select_targets)
    builder.add_node("summarize", summarize)
    builder.add_node("build_system_map", build_system_map)
    builder.add_node("persist_map", persist_map)
    builder.add_node("notify_platform", notify_platform)

    builder.add_edge(START, "select_targets")
    builder.add_conditional_edges("select_targets", route_after_select)
    builder.add_edge("summarize", "build_system_map")
    builder.add_edge("build_system_map", "persist_map")
    builder.add_conditional_edges("persist_map", route_after_persist)
    builder.add_edge("notify_platform", END)

    return builder


graph = build_graph().compile()
