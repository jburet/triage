"""Input to the Analysis sub-graph (architecture §2.1).

Not used by the M1 ticket pipeline, but defined now because it is half of the
contract between the two shared sub-graphs and the fan-out rule in ADR-0005
refers to it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from triage.schemas.common import Filled


class CauseType(StrEnum):
    """Decides which analysis branch a hypothesis is routed to."""

    APP = "app"
    INFRA = "infra"
    DEPLOYMENT = "deployment"
    DEPENDENCY = "dependency"


class Hypothesis(BaseModel):
    cause_type: CauseType
    service: str
    commit: str | None = Field(
        default=None, description="Commit to analyse at; None for dependency causes."
    )
    base_commit: str | None = Field(
        default=None,
        description="Previously deployed commit, for a deployment cause: the diff runs "
        "between it and `commit`. None when only one commit is known.",
    )
    description: Filled
    rank_score: float = Field(ge=0.0, le=1.0, description="Relative plausibility within the set.")
