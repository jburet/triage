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
from triage.mapping.derive import derive_workload
from triage.mapping.resolve import unclaimed
from triage.mapping.seed import load_seed
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.system_map import Derivation, MappingOutcome

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
        derivations.append(derive_workload(deps.config, seed, service, events))
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
