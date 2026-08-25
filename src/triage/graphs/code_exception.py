"""F2 — one gated error group becomes a threaded report (M8, ADR-0025, ADR-0026).

    group_in
      → open_group         (group → analysing; the one thread this group ever uses)
      → collect_exception  (the three collectors, and which nothing they found)
      → qualify_exception  (analysis tier: ranked causes; the paths and the commit
                            are rules, not answers)
      → [analysis]         (shared sub-graph: fan-out, then a Diagnosis)
      → [ticket_pipeline]  (shared sub-graph: dedup, gate, report)
      → settle_group       (reported or open, on the group itself)

The same composition as F1's incident graph, and deliberately the same: F2 owns
its input and its delivery and neither the diagnosis nor the pipeline. What it
does not have is F1's ``classify_alert`` — an error group already names its
exception type and its source location, so there is nothing left to classify —
and no post-mortem, which is an incident's write-up rather than a defect's.

Nothing here decides *whether* to run. The volume gate settled that (ADR-0025)
before the group was handed over.
"""

from typing import cast

from langgraph.graph import END, START, StateGraph

from triage.graphs.analysis import build_graph as build_analysis
from triage.graphs.state import CodeExceptionState
from triage.graphs.ticket_pipeline import build_graph as build_ticket_pipeline
from triage.nodes.code_exception import open_group, settle_group
from triage.nodes.collect_exception import collect_exception
from triage.nodes.qualify_exception import qualify_exception
from triage.runtime import DEPS_KEY, Deps
from triage.schemas.errors import ErrorGroupStatus

CodeExceptionGraph = StateGraph[CodeExceptionState, None, CodeExceptionState, CodeExceptionState]


def build_graph() -> CodeExceptionGraph:
    builder: CodeExceptionGraph = StateGraph(CodeExceptionState)

    builder.add_node("open_group", open_group)
    builder.add_node("collect_exception", collect_exception)
    builder.add_node("qualify_exception", qualify_exception)
    builder.add_node("analysis", build_analysis().compile())
    builder.add_node("ticket_pipeline", build_ticket_pipeline().compile())
    builder.add_node("settle_group", settle_group)

    builder.add_edge(START, "open_group")
    builder.add_edge("open_group", "collect_exception")
    builder.add_edge("collect_exception", "qualify_exception")
    builder.add_edge("qualify_exception", "analysis")
    builder.add_edge("analysis", "ticket_pipeline")
    builder.add_edge("ticket_pipeline", "settle_group")
    builder.add_edge("settle_group", END)

    return builder


graph = build_graph().compile()


async def run_code_exception(
    state: CodeExceptionState, deps: Deps, *, thread_id: str | None = None
) -> CodeExceptionState:
    """Invoke the graph in this process, leaving a crashed group recoverable (4.6).

    The sibling of ``run_incident``, and it differs in what it writes back.
    A signal that dies is ``failed`` and that cycle is over; a group is not over —
    the same defect will be seen again next tick — so the row goes back to
    ``open`` rather than sitting in ``analysing`` where nothing would ever look at
    it again. What stops that becoming a retry loop is already stamped on the row:
    the tick that selected it moved ``analysed_at_cumulative`` and
    ``last_analysed_at``, so the cooldown throttles the next attempt exactly as it
    throttles a second report. The failure is re-raised: a broken Triage must not
    look like a quiet one.
    """
    configurable: dict[str, object] = {DEPS_KEY: deps}
    if thread_id is not None:
        configurable["thread_id"] = thread_id
    try:
        return cast(
            CodeExceptionState, await graph.ainvoke(state, config={"configurable": configurable})
        )
    except Exception:
        group = state.get("group")
        if group is not None:
            stored = await deps.repo.error_group(group.key) or group
            await deps.repo.upsert_error_group(
                stored.model_copy(update={"status": ErrorGroupStatus.OPEN})
            )
        raise
