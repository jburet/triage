"""The confidence gate: a rule, deliberately not a model call.

This is the one place the F1/F3 asymmetry lives (ADR-0002). Keeping it as a pure
function means the threshold that decides whether a team gets interrupted is
readable, testable and identical on every run.
"""

from langchain_core.runnables import RunnableConfig

from triage.config import Config
from triage.graphs.state import TicketPipelineState
from triage.schemas.common import Confidence, Feature


def passes_gate(confidence: Confidence, feature: Feature, config: Config) -> bool:
    return confidence.at_least(config.confidence_threshold(feature))


async def confidence_gate(
    state: TicketPipelineState, config: RunnableConfig | None = None
) -> TicketPipelineState:
    """A no-op node; the decision is made by :func:`route_after_gate` on the edge."""
    return {}
