"""The shared ticket pipeline (architecture §2.2).

Takes a :class:`~triage.schemas.diagnosis.Diagnosis` and produces either a Jira
ticket in `Proposed by agent`, an update to an existing ticket, or a Slack notice
explaining why there is no ticket. Every path records an evaluation row.

Both F1 and F3 will compose this sub-graph; it is built and tested standalone so
that the product definition can be validated before any collector exists.

    diagnosis_in
      → record_diagnosis
      → dedup_check
          ├─ matched → update_existing_ticket → END
          └─ new → confidence_gate
                    ├─ below threshold → notify_below_threshold → END
                    └─ above → compose_ticket → self_review
                                 ├─ passes → create_ticket → END
                                 ├─ fails, budget left → compose_ticket
                                 └─ fails, budget spent → notify_review_exhausted → END
"""

from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from triage.graphs.state import TicketPipelineState
from triage.nodes.compose import compose_ticket
from triage.nodes.confidence import confidence_gate, passes_gate
from triage.nodes.dedup import dedup_check, update_existing_ticket
from triage.nodes.persist import record_diagnosis
from triage.nodes.publish import (
    create_ticket,
    notify_below_threshold,
    notify_review_exhausted,
    publish_report,
)
from triage.nodes.self_review import self_review
from triage.runtime import deps_from_runnable_config


def route_after_dedup(
    state: TicketPipelineState,
) -> Literal["update_existing_ticket", "confidence_gate"]:
    decision = state["dedup"]
    if decision.matched and decision.ticket_key:
        return "update_existing_ticket"
    return "confidence_gate"


def route_after_gate(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> Literal["publish_report", "compose_ticket", "notify_below_threshold"]:
    """Report, or take the Jira path — and only then let the threshold route.

    With ``writes: slack`` the threshold has nothing left to route between: both
    of its destinations are the same channel, so it frames the report instead
    (ADR-0023) and the gate is passed through.
    """
    deps = deps_from_runnable_config(config)
    if not deps.config.files_tickets:
        return "publish_report"
    diagnosis = state["diagnosis"]
    if passes_gate(diagnosis.confidence, diagnosis.feature, deps.config):
        return "compose_ticket"
    return "notify_below_threshold"


def route_after_review(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> Literal["create_ticket", "compose_ticket", "notify_review_exhausted"]:
    """Pass, retry, or give the draft to a human.

    The budget counts *composes*, not retries: attempt three having just failed
    review means the budget is spent, not that a fourth is due.
    """
    if state["verdict"].passes:
        return "create_ticket"
    deps = deps_from_runnable_config(config)
    if state.get("compose_attempts", 0) >= deps.config.thresholds.max_compose_attempts:
        return "notify_review_exhausted"
    return "compose_ticket"


TicketPipelineGraph = StateGraph[
    TicketPipelineState, None, TicketPipelineState, TicketPipelineState
]


def build_graph() -> TicketPipelineGraph:
    builder: TicketPipelineGraph = StateGraph(TicketPipelineState)

    builder.add_node("record_diagnosis", record_diagnosis)
    builder.add_node("dedup_check", dedup_check)
    builder.add_node("update_existing_ticket", update_existing_ticket)
    builder.add_node("confidence_gate", confidence_gate)
    builder.add_node("compose_ticket", compose_ticket)
    builder.add_node("self_review", self_review)
    builder.add_node("create_ticket", create_ticket)
    builder.add_node("notify_below_threshold", notify_below_threshold)
    builder.add_node("notify_review_exhausted", notify_review_exhausted)
    builder.add_node("publish_report", publish_report)

    builder.add_edge(START, "record_diagnosis")
    builder.add_edge("record_diagnosis", "dedup_check")
    builder.add_conditional_edges("dedup_check", route_after_dedup)
    builder.add_edge("update_existing_ticket", END)
    builder.add_conditional_edges("confidence_gate", route_after_gate)
    builder.add_edge("notify_below_threshold", END)
    builder.add_edge("compose_ticket", "self_review")
    builder.add_conditional_edges("self_review", route_after_review)
    builder.add_edge("create_ticket", END)
    builder.add_edge("notify_review_exhausted", END)
    builder.add_edge("publish_report", END)

    return builder


graph = build_graph().compile()
