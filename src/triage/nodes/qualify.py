"""Turning a collection into ranked hypotheses (architecture §2.3, ADR-0016).

This is where the correlation happens — the part Bits AI was going to be bought
for. The model reads what the collectors returned, together with what F0 knows
about the service, and proposes mechanisms.

What it may not do is name a commit. ``ProposedCause`` has no commit field at
all: the deployed commit is looked up in the system map here, and a cause whose
commit cannot be resolved is *changed* rather than annotated — to a dependency
cause when the service is unknown to the map, to an infrastructure cause when it
is known but its deployed version is not. Both are hypotheses that can still be
analysed; a hypothesis carrying an invented commit sends an analysis Job to clone
a ref that does not exist, and its failure looks like an infrastructure problem
rather than a fabrication.
"""

from langchain_core.runnables import RunnableConfig

from triage.graphs.state import IncidentState
from triage.nodes.collect import alert_payload, collection_payload
from triage.prompts import render
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.collection import ProposedCause, Qualification
from triage.schemas.hypothesis import CauseType, Hypothesis

NEEDS_COMMIT = (CauseType.APP, CauseType.DEPLOYMENT)


async def _resolve(deps: Deps, cause: ProposedCause) -> Hypothesis:
    entry = await deps.repo.system_map_for_service(cause.service)
    commit = entry.source_commit if entry else None
    cause_type = cause.cause_type
    if cause_type in NEEDS_COMMIT and not commit:
        cause_type = CauseType.DEPENDENCY if entry is None else CauseType.INFRA
    return Hypothesis(
        cause_type=cause_type,
        service=cause.service,
        commit=commit if cause_type in NEEDS_COMMIT else None,
        description=cause.description,
        rank_score=cause.rank_score,
    )


async def qualify(state: IncidentState, config: RunnableConfig | None = None) -> IncidentState:
    deps = deps_from_runnable_config(config)
    alert = state["alert"]
    service = state.get("service") or alert.scope.workload or ""
    entry = await deps.repo.system_map_for_service(service)

    qualification = await deps.llm.call(
        "analysis",
        render(
            "qualify",
            alert=alert_payload(alert),
            collected=collection_payload(state["collection"], deps.config.collection),
            system_map=(
                entry.model_dump(mode="json")
                if entry
                else {
                    "service": service,
                    "known": False,
                    "note": "F0 has no cartography for this workload: its repository, "
                    "entry points and dependencies are unknown.",
                }
            ),
        ),
        Qualification,
    )
    hypotheses = [await _resolve(deps, cause) for cause in qualification.causes]
    return {"qualification": qualification, "hypotheses": hypotheses}
