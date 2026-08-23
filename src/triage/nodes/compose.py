"""Compose the ticket from the diagnosis."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from triage.graphs.state import TicketPipelineState
from triage.prompts import render
from triage.runtime import deps_from_runnable_config
from triage.schemas.ticket import TicketDraft


async def compose_ticket(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """Render a diagnosis into the nine specification sections.

    On a retry the previous draft and the reviewer's feedback are both included:
    without the draft the model rewrites from scratch and loses the sections that
    already passed, and without the feedback it reproduces the same gap.
    """
    deps = deps_from_runnable_config(config)
    attempts = state.get("compose_attempts", 0)

    sections: dict[str, Any] = {"diagnosis": state["diagnosis"].model_dump(mode="json")}
    verdict = state.get("verdict")
    if verdict is not None and not verdict.passes:
        sections["previous_draft"] = state["draft"].model_dump(mode="json")
        sections["reviewer_feedback"] = {
            "failing_sections": [section.value for section in verdict.missing],
            "feedback": verdict.feedback,
        }

    draft = await deps.llm.call("analysis", render("compose_ticket", **sections), TicketDraft)
    return {"draft": draft, "compose_attempts": attempts + 1}
