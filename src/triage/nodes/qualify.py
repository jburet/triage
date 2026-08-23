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

import structlog
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from triage.graphs.state import IncidentState
from triage.llm import StructuredOutputError
from triage.nodes.collect import alert_payload, collection_payload
from triage.prompts import render
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.collection import ProposedCause, Qualification
from triage.schemas.hypothesis import CauseType, Hypothesis
from triage.scope import deployed_repo

log = structlog.get_logger(__name__)

NEEDS_COMMIT = (CauseType.APP, CauseType.DEPLOYMENT)


async def _resolve(deps: Deps, cause: ProposedCause) -> Hypothesis:
    repo_url, commit = await deployed_repo(deps.config, deps.repo, cause.service)
    cause_type = cause.cause_type
    if cause_type in NEEDS_COMMIT and not commit:
        cause_type = CauseType.DEPENDENCY if repo_url is None else CauseType.INFRA
    return Hypothesis(
        cause_type=cause_type,
        service=cause.service,
        commit=commit if cause_type in NEEDS_COMMIT else None,
        description=cause.description,
        rank_score=cause.rank_score,
    )


async def _qualified(deps: Deps, sections: dict[str, object]) -> Qualification:
    """Ask once; ask again with the error. Then let it fail.

    Everything downstream is built from the causes, so an answer that does not
    parse costs the collection that produced it. The one observed failure —
    the causes written as markup inside the summary — is exactly the kind a model
    fixes when shown the validator's complaint.
    """
    try:
        return await deps.llm.call("analysis", render("qualify", **sections), Qualification)
    except (StructuredOutputError, ValidationError) as exc:
        log.warning("qualification_rejected", error=str(exc))
        sections["correction"] = (
            f"Your previous answer did not satisfy the schema and was discarded. "
            f"Answer again, putting each cause in the `causes` list as a separate "
            f"object — never as text inside `summary`. The validator reported:\n{exc}"
        )
    return await deps.llm.call("analysis", render("qualify", **sections), Qualification)


async def qualify(state: IncidentState, config: RunnableConfig | None = None) -> IncidentState:
    deps = deps_from_runnable_config(config)
    alert = state["alert"]
    service = state.get("service") or alert.scope.workload or ""
    entry = await deps.repo.system_map_for_service(service)

    sections: dict[str, object] = {
        "alert": alert_payload(alert),
        "collected": collection_payload(state["collection"], deps.config.collection),
        "system_map": (
            entry.model_dump(mode="json")
            if entry
            else {
                "service": service,
                "known": False,
                "note": "F0 has no cartography for this workload: its repository, "
                "entry points and dependencies are unknown.",
            }
        ),
    }
    qualification = await _qualified(deps, sections)
    hypotheses = [await _resolve(deps, cause) for cause in qualification.causes]
    return {
        "qualification": qualification,
        "hypotheses": hypotheses,
        # What the shared Analysis sub-graph is given as context: it must not have
        # to know that an F1 collection is what produced these hypotheses.
        "context": {
            "alert": alert_payload(alert),
            "telemetry_summary": qualification.summary,
            "collected": collection_payload(state["collection"], deps.config.collection),
        },
    }
