"""F1's collection nodes: classify, sweep, then ask for more (ADR-0016).

The division of labour is the point. The ``triage`` tier decides one thing — what
class of failure this alert describes — and everything else about the collection
is a rule: the window is arithmetic on the monitor's own evaluation window, the
collectors are the class's recipe, and the caps are ``config.yaml``'s. A model
that cannot decide the class gets ``generic``, which still sweeps; a model call
that fails outright gets the same, because an alert nobody classified is still an
alert somebody has to look at.

The follow-up loop is the one place the ``analysis`` tier chooses a query, and it
is bounded twice over: by the call budget, and by the collector set — a request
naming something Triage does not have is discarded and written down rather than
translated into a best guess.
"""

from typing import Any, Literal

import structlog
from langchain_core.runnables import RunnableConfig

from triage.collect.budget import fit
from triage.collect.recipes import collection_window
from triage.collect.sweep import follow_up as run_follow_up
from triage.collect.sweep import sweep
from triage.config import CollectionConfig
from triage.graphs.state import IncidentState
from triage.integrations.datadog import DatadogClient, DatadogError
from triage.prompts import render
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.alert import Alert
from triage.schemas.collection import (
    AlertClass,
    AlertClassification,
    Collection,
    Collector,
    FollowUpPlan,
)

log = structlog.get_logger(__name__)

UNCLASSIFIED = AlertClassification(
    alert_class=AlertClass.GENERIC,
    reason="The alert could not be classified, so the shared sweep was collected instead.",
)


def alert_payload(alert: Alert) -> dict[str, Any]:
    """What a prompt is shown about an alert: the monitor's own words, not the raw event."""
    return {
        "monitor": alert.monitor_name,
        "query": alert.monitor_query,
        "thresholds": (alert.monitor_options or {}).get("thresholds"),
        "priority": alert.priority,
        "group": alert.group,
        "status": alert.status.value,
        "fired_at": alert.fired_at,
        "scope": alert.scope.model_dump(),
        "tags": alert.tags,
    }


def collection_payload(collection: Collection, config: CollectionConfig) -> dict[str, Any]:
    """The collection as a prompt sees it: reduced, then cut to the byte budget."""
    return fit(collection, config.max_prompt_bytes).as_payload()


async def _with_monitor(client: DatadogClient, alert: Alert) -> Alert:
    """Read the monitor only when the event did not carry it (ADR-0016).

    A monitor alert event already includes the query, the thresholds, the
    renotify options, the priority and the groups. Reading the monitor anyway
    would spend a call on every incident to learn what is already in hand.
    """
    if alert.monitor_query or alert.monitor_id is None:
        return alert
    try:
        monitor = await client.get_monitor(alert.monitor_id)
    except (DatadogError, ValueError, KeyError) as exc:
        log.warning("monitor_read_failed", monitor=alert.monitor_id, error=str(exc))
        return alert
    return alert.model_copy(
        update={
            "monitor_query": monitor.get("query") or alert.monitor_query,
            "monitor_name": monitor.get("name") or alert.monitor_name,
            "monitor_options": monitor.get("options") or alert.monitor_options,
            "priority": monitor.get("priority", alert.priority),
        }
    )


async def classify_alert(
    state: IncidentState, config: RunnableConfig | None = None
) -> IncidentState:
    deps = deps_from_runnable_config(config)
    alert = await _with_monitor(deps.datadog, state["alert"])
    try:
        classification = await deps.llm.call(
            "triage", render("classify_alert", alert=alert_payload(alert)), AlertClassification
        )
    except Exception as exc:
        log.warning("alert_not_classified", error=str(exc))
        classification = UNCLASSIFIED
    return {
        "alert": alert,
        "classification": classification,
        "window": collection_window(alert, deps.config.collection),
    }


async def collect(state: IncidentState, config: RunnableConfig | None = None) -> IncidentState:
    deps = deps_from_runnable_config(config)
    alert_class = state["classification"].alert_class
    window = state["window"]
    results = await sweep(deps.datadog, state["alert"], alert_class, window, deps.config.collection)
    return {
        "collection": Collection(alert_class=alert_class, window=window, results=results),
        "followup_done": False,
    }


async def follow_up(state: IncidentState, config: RunnableConfig | None = None) -> IncidentState:
    deps = deps_from_runnable_config(config)
    collection = state["collection"]
    caps = deps.config.collection
    if collection.followup_calls >= caps.max_followup_calls:
        return {"followup_done": True}

    plan = await _plan_follow_up(deps, state, collection)
    if plan is None:
        return {
            "collection": collection.model_copy(
                update={"refused": [*collection.refused, PLAN_UNREADABLE]}
            ),
            "followup_done": True,
        }
    if not plan.requests:
        return {"followup_done": True}

    results, refused = await run_follow_up(
        deps.datadog,
        state["alert"],
        plan.requests,
        state["window"],
        caps,
        already_spent=collection.followup_calls,
    )
    spent = collection.followup_calls + len(results)
    return {
        "collection": collection.model_copy(
            update={
                "results": [*collection.results, *results],
                "followup_calls": spent,
                "refused": [*collection.refused, *refused],
            }
        ),
        "followup_done": not results or spent >= caps.max_followup_calls,
    }


PLAN_UNREADABLE = (
    "The follow-up plan could not be read: the model's answer did not satisfy FollowUpPlan, "
    "so no further collection was planned. What follows is the fixed sweep and nothing else — "
    "not a judgement that the sweep was sufficient."
)
"""Said in the collection, because the diagnosis is written from the collection.

An unreadable plan and an empty one leave the same state — nothing more
collected, the loop over — and mean opposite things: one is the model saying the
sweep answers the question, the other is Triage losing the calls it asked for.
"""


async def _plan_follow_up(
    deps: Deps, state: IncidentState, collection: Collection
) -> FollowUpPlan | None:
    try:
        return await deps.llm.call(
            "analysis",
            render(
                "follow_up",
                alert=alert_payload(state["alert"]),
                collectors=[collector.value for collector in Collector],
                budget={
                    "spent": collection.followup_calls,
                    "remaining": deps.config.collection.max_followup_calls
                    - collection.followup_calls,
                },
                collected=collection_payload(collection, deps.config.collection),
            ),
            FollowUpPlan,
        )
    except Exception as exc:
        log.warning("follow_up_not_planned", error=str(exc))
        return None


def route_after_follow_up(state: IncidentState) -> Literal["follow_up", "qualify"]:
    return "qualify" if state.get("followup_done") else "follow_up"
