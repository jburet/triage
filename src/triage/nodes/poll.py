"""One tick of the alert poller (ADR-0017, ADR-0018, ADR-0011).

F1 has no ingress. A cron polls Datadog's event stream every 60 seconds, which
costs a component less than a webhook and — the part that actually decided it —
is the only shape that can express the persistence gate: "is this alert *still*
firing fifteen minutes later" is a question a poller answers for free and a push
endpoint cannot answer at all.

Three rules run here, in this order.

**Deduplicate, do not track.** Each tick queries from the watermark minus two
minutes and deduplicates on the Datadog event id, because a cursor that has to be
exact against ingestion lag is a cursor that silently loses alerts. One signal per
(monitor, firing group) cycle; re-notifications of an open cycle create nothing.

**Resolve an owner by pattern.** A firing group is a per-customer tenant, so
enumeration is impossible; and the environment comes from the cluster map, never
from an ``env:`` tag, because no alert carries a usable one.

**Wait before spending anything.** Across 961 measured pod-down cycles the
longest was nine minutes. A cycle that recovers before the gate is recorded with
its duration and never analysed — but the recoveries are *counted*, because the
incident that motivated all of this (a liveness probe shorter than the pod's own
startup) shows up as many short cycles and never as one long one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from triage.config import Config
from triage.graphs.state import IncidentState, PollerState
from triage.nodes.flapping import report_flapping
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.alert import Alert, AlertStatus
from triage.schemas.common import Feature
from triage.schemas.signal import Signal, SignalStatus
from triage.scope import Routing, resolve

log = structlog.get_logger(__name__)

POLLER_NAME = "datadog_alerts"
ALERT_QUERY = "source:alert"
OVERLAP = timedelta(minutes=2)
CATCH_UP_LIMIT = timedelta(minutes=30)
INCIDENT_ASSISTANT = "incident"
PAGE_LIMIT = 200


def _thread_id(signal: Signal) -> str:
    """The same thread id whether the Platform runs it or this process does."""
    return f"incident-{signal.signal_id}"


def _signal_from(alert: Alert, routing: Routing, status: SignalStatus) -> Signal:
    return Signal(
        feature=Feature.F1,
        source="datadog",
        external_id=alert.event_id,
        service=routing.service or alert.scope.workload or "unknown",
        team=routing.team,
        monitor_id=alert.monitor_id,
        group=alert.group,
        cycle_key=alert.cycle_key,
        fired_at=alert.fired_at,
        status=status,
        payload=alert.raw,
    )


async def _open_cycle(deps: Deps, alert: Alert) -> Signal | None:
    """The signal for this cycle, if one is still open — a re-notification's home."""
    for signal in await deps.repo.signals_for_cycle(alert.monitor_id, alert.group):
        if signal.status in (SignalStatus.WAITING, SignalStatus.ANALYSING):
            return signal
    return None


async def _record_recovery(deps: Deps, alert: Alert, state: PollerState) -> None:
    """A cycle ended. Record how long it lasted, and whether it ever got analysed."""
    signal = await _open_cycle(deps, alert)
    if signal is None or signal.fired_at is None:
        return
    duration = (alert.fired_at - signal.fired_at).total_seconds()
    status = SignalStatus.SELF_RECOVERED if signal.status is SignalStatus.WAITING else signal.status
    updated = await deps.repo.update_signal(
        signal.model_copy(
            update={
                "recovered_at": alert.fired_at,
                "duration_seconds": duration,
                "status": status,
            }
        )
    )
    if status is SignalStatus.SELF_RECOVERED:
        state.setdefault("recovered", []).append(updated)


async def _ingest(deps: Deps, alert: Alert, config: Config, state: PollerState) -> None:
    if alert.event_id and await deps.repo.signal_by_external_id(alert.event_id):
        return
    if await _open_cycle(deps, alert) is not None:
        # A re-notification of a cycle already being waited on or analysed.
        return

    routing = resolve(config, alert)
    if not routing.in_scope:
        signal = await deps.repo.save_signal(
            _signal_from(alert, routing, SignalStatus.OUT_OF_SCOPE)
        )
        log.info("alert_out_of_scope", signal=str(signal.signal_id), reason=routing.reason)
        state.setdefault("out_of_scope", []).append(signal.signal_id)
        return

    signal = await deps.repo.save_signal(_signal_from(alert, routing, SignalStatus.WAITING))
    state.setdefault("created", []).append(signal.signal_id)


def _gate_reached(signal: Signal, config: Config, now: datetime) -> bool:
    """Has it been firing long enough to be worth analysing (ADR-0018)?"""
    if signal.fired_at is None:
        return True
    minutes = config.persistence_minutes(signal.team, _priority_of(signal.payload))
    return now - signal.fired_at >= timedelta(minutes=minutes)


async def _launch(deps: Deps, signal: Signal, alert: Alert) -> None:
    """Create the run, on the Platform or in this process — same graph, same thread."""
    payload: IncidentState = {
        "alert": alert,
        "signal": signal,
        "team": signal.team or "",
        "service": signal.service,
    }
    if deps.platform is not None:
        await deps.platform.create_run(
            assistant_id=INCIDENT_ASSISTANT,
            thread_id=_thread_id(signal),
            payload={
                "alert": alert.model_dump(mode="json"),
                "signal": signal.model_dump(mode="json"),
                "team": signal.team or "",
                "service": signal.service,
            },
        )
        return
    from triage.graphs.incident import run_incident

    await run_incident(payload, deps, thread_id=_thread_id(signal))


async def _open_the_gate(deps: Deps, config: Config, now: datetime, state: PollerState) -> None:
    """Analyse what is still firing, refuse what nobody can locate (ADR-0018)."""
    for signal in await deps.repo.open_signals():
        if signal.status is not SignalStatus.WAITING or not _gate_reached(signal, config, now):
            continue
        alert = Alert.from_event(signal.payload) if signal.payload else None
        if alert is None:
            continue
        if await deps.repo.system_map_for_service(signal.service) is None:
            await _notify_unmapped(deps, signal)
            state.setdefault("unmapped", []).append(signal.signal_id)
            continue
        await _claim_and_launch(deps, signal, alert)
        state.setdefault("launched", []).append(signal.signal_id)


async def _claim_and_launch(deps: Deps, signal: Signal, alert: Alert) -> None:
    """Take the signal out of `waiting` first, then launch.

    On the Platform, ``create_run`` answers as soon as the run is queued, and the
    run marks the signal itself — later. A signal still `waiting` when the next
    tick reads it is launched again, so a cycle that keeps firing buys a run per
    minute. The poller claims it here instead, and puts it back if the launch
    fails, because a signal nothing is analysing must not sit in `analysing`.
    """
    claimed = await deps.repo.update_signal(
        signal.model_copy(update={"status": SignalStatus.ANALYSING})
    )
    try:
        await _launch(deps, claimed, alert)
    except Exception:
        await deps.repo.update_signal(claimed.model_copy(update={"status": SignalStatus.WAITING}))
        raise


async def _notify_unmapped(deps: Deps, signal: Signal) -> None:
    """A team owns it, but F0 has never seen it: say so, and analyse nothing.

    Running the analysis anyway would produce a diagnosis with no repository, no
    commit and no code read — an expensive way to restate the alert.
    """
    team = signal.team or ""
    channel = (
        deps.config.team(team).slack_channel
        if deps.config.declares_team(team)
        else deps.config.platform_channel()
    )
    await deps.slack.post(
        channel=channel,
        text=(
            f":grey_question: `{signal.service}` has been alerting for the persistence "
            f"window, but F0 has no cartography for it — no repository, so nothing to "
            f"analyse. Add it to `config.yaml` and it will be picked up on the next run."
        ),
    )
    await deps.repo.update_signal(signal.model_copy(update={"status": SignalStatus.DISCARDED}))


async def poll_alerts(state: PollerState, config: RunnableConfig | None = None) -> PollerState:
    """One tick: read the stream, gate what is open, launch what has persisted."""
    deps = deps_from_runnable_config(config)
    now = state.get("now") or datetime.now(UTC)
    result: PollerState = {"now": now}

    start, skipped = await _window(deps, now)
    if skipped is not None:
        result["skipped_span"] = skipped

    page = await deps.datadog.search_events(query=ALERT_QUERY, frm=start, to=now, limit=PAGE_LIMIT)
    events = [event for event in page.get("data", []) or [] if Alert.is_monitor_alert(event)]
    result["events_seen"] = len(events)

    for event in events:
        alert = Alert.from_event(event)
        if alert.status is AlertStatus.OK:
            await _record_recovery(deps, alert, result)
        elif alert.status.is_firing:
            await _ingest(deps, alert, deps.config, result)

    await _open_the_gate(deps, deps.config, now, result)
    result["flapping"] = await report_flapping(deps, deps.config, now, result.get("recovered", []))

    await deps.repo.set_watermark(POLLER_NAME, now)
    return result


async def _window(deps: Deps, now: datetime) -> tuple[datetime, str | None]:
    """Where to read from, and what was skipped if the poller was down too long.

    A bounded catch-up rather than an unbounded replay: an alert three hours old
    is not worth a ticket, and being silent about having skipped it is worse than
    saying so (ADR-0017).
    """
    watermark = await deps.repo.get_watermark(POLLER_NAME)
    if watermark is None:
        return now - OVERLAP, None
    if now - watermark <= CATCH_UP_LIMIT:
        return watermark - OVERLAP, None
    start = now - CATCH_UP_LIMIT
    skipped = (
        f"{watermark.isoformat()} .. {start.isoformat()} "
        f"({int((start - watermark).total_seconds() // 60)} minutes)"
    )
    await deps.slack.post(
        channel=deps.config.platform_channel(),
        text=(
            f":hourglass: The alert poller was behind by "
            f"{int((now - watermark).total_seconds() // 60)} minutes and replayed only the "
            f"last {int(CATCH_UP_LIMIT.total_seconds() // 60)}. Alerts between {skipped} "
            f"were not read."
        ),
    )
    return start, skipped


def _priority_of(payload: dict[str, Any]) -> int | None:
    inner = (payload.get("attributes", {}) or {}).get("attributes", {}) or {}
    priority = (inner.get("monitor", {}) or {}).get("priority")
    return priority if isinstance(priority, int) else None
