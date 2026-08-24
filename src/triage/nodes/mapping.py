"""Deriving the service map: read the cluster's own events, decide, write down.

No model call anywhere on this path. Everything the derivation joins is already
structured, so a tier call here would add spend, latency and a way to be wrong
to a lookup that is none of those — the same reasoning that keeps the F0 merge a
rule (architecture §2.5).

A service the pass cannot map is not an exception: it is a line in the result
saying why, next to the ones it did map.
"""

from datetime import UTC, datetime, timedelta

import structlog
from langchain_core.runnables import RunnableConfig

from triage.graphs.state import MappingState
from triage.integrations.datadog import DatadogError
from triage.mapping.commits import with_deployed_commit
from triage.mapping.derive import derive_workload
from triage.mapping.resolve import unclaimed
from triage.mapping.seed import load_seed
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.system_map import Derivation, MappingOutcome, WorkloadEntry

log = structlog.get_logger(__name__)

LOOKBACK_DAYS = 7
"""How far back a pass looks for a workload's own change events."""


async def select_services(
    state: MappingState, config: RunnableConfig | None = None
) -> MappingState:
    """The services to derive, and the seed to derive them against.

    Given none, the pass covers every service that has alerted recently: those
    are exactly the ones whose mapping Triage has needed and may have missed.
    """
    deps = deps_from_runnable_config(config)
    seed = load_seed()
    named = state.get("services") or []
    if named:
        targets = list(dict.fromkeys(named))
    else:
        since = datetime.now(UTC) - timedelta(days=state.get("lookback_days") or LOOKBACK_DAYS)
        targets = await deps.repo.services_seen_since(since)
    return {
        "seed": seed,
        "targets": targets,
        "unclaimed": unclaimed(deps.config, seed),
    }


async def _events(deps: Deps, service: str, days: int) -> list[dict[str, object]]:
    now = datetime.now(UTC)
    response = await deps.datadog.search_events(
        query=f"service:{service}", frm=now - timedelta(days=days), to=now
    )
    data = response.get("data") or []
    return [event for event in data if isinstance(event, dict)]


async def _with_deployed_commit(deps: Deps, derivation: Derivation) -> Derivation:
    """Ask GitHub which commit this build was cut from, when the image did not say."""
    entry = derivation.entry
    if entry is None or not derivation.mapped:
        return derivation
    return derivation.model_copy(
        update={"entry": await with_deployed_commit(deps.github, deps.config, entry)}
    )


def _against_what_is_on_record(
    derivation: Derivation, previous: WorkloadEntry | None
) -> Derivation:
    """Nothing moved, so nothing is rewritten — ADR-0015's reasoning, one level down.

    The digest is what makes a derivation *an observation*: while it is the same,
    the workload is running the same build and the row on record already says so.
    A pattern mapping has no digest and therefore no such claim, so it is left to
    be written; and a row that differs in anything else is rewritten, because
    config.yaml can move under a digest that did not.
    """
    entry = derivation.entry
    if entry is None or not derivation.mapped or entry.image_digest is None:
        return derivation
    if previous != entry:
        return derivation
    return derivation.model_copy(
        update={
            "outcome": MappingOutcome.UNCHANGED,
            "reason": (
                f"{derivation.service} is still running {entry.image_digest}, which is what "
                f"the mapping on record already says, so nothing was rewritten"
            ),
        }
    )


async def derive_workloads(
    state: MappingState, config: RunnableConfig | None = None
) -> MappingState:
    """One derivation per target service, from that service's own events."""
    deps = deps_from_runnable_config(config)
    seed = state.get("seed") or load_seed()
    days = state.get("lookback_days") or LOOKBACK_DAYS

    derivations: list[Derivation] = []
    for service in state.get("targets", []):
        try:
            events = await _events(deps, service, days)
        except DatadogError as error:
            derivations.append(
                Derivation(
                    service=service,
                    outcome=MappingOutcome.NOT_MAPPED,
                    reason=f"Datadog would not answer for {service}: {error}",
                )
            )
            continue
        derived = await _with_deployed_commit(
            deps, derive_workload(deps.config, seed, service, events)
        )
        derivations.append(
            _against_what_is_on_record(derived, await deps.repo.workload_for_service(service))
        )
    return {"derivations": derivations}


async def persist_workloads(
    state: MappingState, config: RunnableConfig | None = None
) -> MappingState:
    """Write the mappings this pass established; a conflict or a gap writes nothing."""
    deps = deps_from_runnable_config(config)
    written = 0
    for derivation in state.get("derivations", []):
        if derivation.entry is None or not derivation.mapped:
            continue
        await deps.repo.upsert_workload(derivation.entry)
        written += 1
    return {"entries_written": written}
