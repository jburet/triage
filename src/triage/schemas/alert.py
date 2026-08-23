"""A Datadog monitor alert, as the poller and the collectors need it (ADR-0017).

Parsed from one ``/api/v2/events/search`` result, because that is where F1's
alerts come from and because the event already carries what would otherwise cost
a monitor read: the query, the thresholds, the renotify options, the priority,
the firing group and the links back into Datadog.

Two things measured on the real org shape this model. There is no usable ``env:``
tag — the environment lives inside ``kube_cluster_name`` — and there is often no
``service:`` tag either, so the scope keeps every identifier it found rather than
collapsing them into one name. Whoever resolves an owner from that decides which
identifier wins; this object only refuses to invent one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

MONITOR_ALERT_KEY = "monitor-alert-event"


class AlertStatus(StrEnum):
    """The transition this event records, not the monitor's current state."""

    ERROR = "error"
    WARN = "warn"
    OK = "ok"
    NO_DATA = "no_data"

    @property
    def is_firing(self) -> bool:
        return self in (AlertStatus.ERROR, AlertStatus.NO_DATA)


_STATUS_ALIASES: dict[str, AlertStatus] = {
    "error": AlertStatus.ERROR,
    "alert": AlertStatus.ERROR,
    "warning": AlertStatus.WARN,
    "warn": AlertStatus.WARN,
    "success": AlertStatus.OK,
    "ok": AlertStatus.OK,
    "recovery": AlertStatus.OK,
    "no data": AlertStatus.NO_DATA,
    "no_data": AlertStatus.NO_DATA,
    "nodata": AlertStatus.NO_DATA,
}


class AlertScope(BaseModel):
    """Every identifier the alert carried. Any of them may be the only one there is."""

    service: str | None = None
    namespace: str | None = None
    stateful_set: str | None = None
    cluster: str | None = None
    pod: str | None = None

    @property
    def workload(self) -> str | None:
        """The best available name for what broke, in decreasing specificity."""
        return self.service or self.stateful_set or self.namespace


def _tag_map(tags: list[str]) -> dict[str, str]:
    pairs = (tag.split(":", 1) for tag in tags if ":" in tag)
    return {key: value for key, value in pairs}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class Alert(BaseModel):
    """One monitor transition: what fired, for which group, when, and how to re-read it."""

    event_id: str
    monitor_id: int | None = None
    monitor_name: str = ""
    monitor_query: str | None = None
    monitor_options: dict[str, Any] = Field(default_factory=dict)
    cycle_key: str | None = Field(
        default=None,
        description="Datadog's own key for one firing cycle: re-notifications share it.",
    )
    group: str | None = Field(default=None, description="The firing group, as Datadog renders it.")
    status: AlertStatus = AlertStatus.ERROR
    priority: int | None = None
    fired_at: datetime
    tags: list[str] = Field(default_factory=list)
    scope: AlertScope = Field(default_factory=AlertScope)
    alert_url: str | None = None
    logs_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """What one alert cycle is keyed on (ADR-0017): the monitor and the group."""
        return f"{self.monitor_id}:{self.group or ''}"

    @classmethod
    def is_monitor_alert(cls, event: dict[str, Any]) -> bool:
        inner = event.get("attributes", {}).get("attributes", {})
        return MONITOR_ALERT_KEY in inner or "monitor" in inner

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> Alert:
        attributes = event.get("attributes", {})
        inner = attributes.get("attributes", {})
        monitor = inner.get("monitor", {}) or {}
        transition = inner.get(MONITOR_ALERT_KEY, {}) or {}
        tags = [str(tag) for tag in attributes.get("tags", []) or []]
        groups = [str(group) for group in monitor.get("groups", []) or []]
        by_key = {**_tag_map(tags), **_tag_map(groups)}

        raw_status = str(
            transition.get("alert_type") or attributes.get("status") or inner.get("status") or ""
        ).lower()
        fired_at = (
            _parse_time(attributes.get("timestamp"))
            or _parse_time(transition.get("date_happened"))
            or datetime.now(UTC)
        )
        result = monitor.get("result", {}) or {}
        return cls(
            event_id=str(
                (inner.get("evt") or {}).get("id") or transition.get("id") or event.get("id", "")
            ),
            monitor_id=monitor.get("id"),
            monitor_name=str(monitor.get("name", "")),
            monitor_query=monitor.get("query"),
            monitor_options=monitor.get("options", {}) or {},
            cycle_key=monitor.get("alert_cycle_key_txt") or transition.get("alert_cycle_key"),
            group=",".join(groups) or None,
            status=_STATUS_ALIASES.get(raw_status, AlertStatus.ERROR),
            priority=monitor.get("priority"),
            fired_at=fired_at,
            tags=tags,
            scope=AlertScope(
                service=by_key.get("service"),
                namespace=by_key.get("kube_namespace"),
                stateful_set=by_key.get("kube_stateful_set"),
                cluster=by_key.get("kube_cluster_name"),
                pod=by_key.get("pod_name"),
            ),
            alert_url=result.get("alert_url"),
            logs_url=result.get("logs_url"),
            raw=event,
        )
