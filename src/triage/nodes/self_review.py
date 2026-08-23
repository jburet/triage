"""The gate that asks whether a developer could actually start on this."""

from langchain_core.runnables import RunnableConfig

from triage.graphs.state import TicketPipelineState
from triage.prompts import render
from triage.runtime import deps_from_runnable_config
from triage.schemas.ticket import ReviewVerdict


async def self_review(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Review the draft against the diagnosis it came from.

    The diagnosis is supplied alongside the draft on purpose: the failure this
    catches that no amount of proofreading would is the composer asserting
    something the diagnosis never said.
    """
    deps = deps_from_runnable_config(config)
    verdict = await deps.llm.call(
        "diagnosis",
        render(
            "self_review",
            draft=state["draft"].model_dump(mode="json"),
            diagnosis=state["diagnosis"].model_dump(mode="json"),
        ),
        ReviewVerdict,
    )
    return {"verdict": verdict}
