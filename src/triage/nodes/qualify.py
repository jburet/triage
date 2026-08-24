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

import json

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
    deployment = await deployed_repo(deps.config, deps.repo, cause.service)
    repo_url, commit = deployment.repo_url, deployment.commit
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


ATTEMPTS = 3
"""Measured, not chosen: against the real collection one ask in two comes back malformed.

Eight calls of the live `qualify` prompt returned a valid `Qualification` four
times (ADR-0022). At that rate two asks lose one run in four and three lose one
in eight, which is the difference between an alert class nobody trusts and one
that mostly works. A fourth ask buys a sixteenth for another `analysis` call on
every failure, and the notice already covers what gets through.
"""


async def _qualified(deps: Deps, sections: dict[str, object]) -> Qualification:
    """Ask, and ask again with the shape, up to ``ATTEMPTS`` times.

    Everything downstream is built from the causes, so an answer that does not
    parse costs the collection that produced it.

    The correction states the shape the tool call must have, not the mistake that
    was made. On 2026-08-24, against the first real collection, the first answer
    wrote the causes as markup inside the summary and a correction naming *that*
    bought a second answer that failed differently — one cause's fields flattened
    into the wrapper. Both are `causes` absent; only the second was a surprise.
    """
    for attempt in range(ATTEMPTS):
        try:
            return await deps.llm.call("analysis", render("qualify", **sections), Qualification)
        except (StructuredOutputError, ValidationError) as exc:
            log.warning("qualification_rejected", attempt=attempt + 1, error=str(exc))
            if attempt == ATTEMPTS - 1:
                raise
            sections["correction"] = (
                f"Your previous answer did not satisfy the schema and was discarded. The "
                f"tool call must carry a top-level `causes` array, holding one object per "
                f"cause with its own `cause_type`, `service`, `description` and "
                f"`rank_score`. Do not merge a cause's fields into the wrapper alongside "
                f"`summary`, and never write the causes as text or markup inside `summary`. "
                f"The validator reported:\n{exc}"
            )
    raise AssertionError("unreachable: the last attempt either returns or raises")


async def _hand_to_the_team(deps: Deps, state: IncidentState, service: str, exc: Exception) -> None:
    """Say what was collected and that nothing will be made of it.

    The collection is what the run is worth: real Datadog calls over a window
    that closes, reduced and budgeted. Letting a schema failure take it down with
    the run leaves the team an alert and nothing else, when what Triage holds is
    exactly the telemetry they would have gone and read.

    The notice says this is Triage's own failure. The exception is re-raised
    after it, on ``run_incident``'s reasoning: a broken Triage must not look like
    a quiet one.
    """
    team = state.get("team")
    channel = deps.config.team(team).slack_channel if team else deps.config.platform_channel()
    collected = collection_payload(state["collection"], deps.config.collection)
    await deps.slack.post(
        channel=channel,
        text=(
            f":rotating_light: Triage could not qualify `{service}` — the model returned no "
            f"`causes` twice, so there is no analysis and no ticket for this alert.\n"
            f"*Validator:* {exc}\n"
            f"The collection is attached and complete; this is Triage's own failure, not a "
            f"quiet incident."
        ),
        attachment=json.dumps(collected, indent=2, default=str),
        thread_ts=state.get("thread_ts"),
    )


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
    try:
        qualification = await _qualified(deps, sections)
    except (StructuredOutputError, ValidationError) as exc:
        await _hand_to_the_team(deps, state, service, exc)
        raise
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
