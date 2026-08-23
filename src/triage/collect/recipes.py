"""What to collect for an alert, and over what window (ADR-0016).

Two rules and a table. The *window* is arithmetic on the monitor's own
evaluation window, not a model's opinion — an alert that evaluates over five
minutes is not explained by six hours of context, and one that evaluates over an
hour is not explained by fifteen minutes. The *collectors* are a recipe per alert
class, so the only judgement left to a model is which class the alert is in, and
an alert it cannot classify falls to ``generic`` and is still swept.

The monitor's own query is the best description of what broke, but it is only
re-runnable in the idiom it was written in: a metric monitor's threshold
expression goes to the timeseries API, an event monitor's to event search. Sent
to the wrong one it returns a 400, which reads exactly like "no data" — which is
why the idiom is matched here rather than guessed at the call site.

Only ``crash_restart`` is written from a captured incident. The other recipes are
written from the shape of that one and are the open risk on the M3 plan: capture
a real alert per class before trusting them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from triage.config import CollectionConfig
from triage.schemas.alert import Alert, AlertScope
from triage.schemas.collection import AlertClass, Collector
from triage.schemas.common import TimeWindow

METRIC_QUERY = re.compile(r"^\w+\([^)]*\):(?P<expr>.+?)\s*(?:<=|>=|<|>|==|!=)\s*[-\d.]+\s*$", re.S)
EVENT_QUERY = re.compile(r'^events\("(?P<expr>.*?)"\)\.', re.S)
LOG_QUERY = re.compile(r'^logs\("(?P<expr>.*?)"\)\.', re.S)
EVALUATION_WINDOW = re.compile(r'last_(\d+)([smhd])|\.last\("(\d+)([smhd])"\)')

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True)
class MonitorQueryPlan:
    """How the monitor's own query is re-run, or why it cannot be."""

    idiom: str
    query: str


def evaluation_window(query: str | None) -> timedelta | None:
    """The monitor's own evaluation window, from either query idiom."""
    if not query:
        return None
    match = EVALUATION_WINDOW.search(query)
    if match is None:
        return None
    amount, unit = (
        (match.group(1), match.group(2))
        if match.group(1)
        else (
            match.group(3),
            match.group(4),
        )
    )
    return timedelta(seconds=int(amount) * _UNITS[unit])


def collection_window(
    alert: Alert, config: CollectionConfig, now: datetime | None = None
) -> TimeWindow:
    """The monitor's evaluation window times the multiplier, clamped, around the firing."""
    evaluated = evaluation_window(alert.monitor_query) or timedelta(
        minutes=config.window_min_minutes
    )
    span = evaluated * config.window_multiplier
    span = max(span, timedelta(minutes=config.window_min_minutes))
    span = min(span, timedelta(hours=config.window_max_hours))
    moment = now or datetime.now(UTC)
    return TimeWindow(start=alert.fired_at - span, end=min(moment, alert.fired_at + span))


def monitor_query_plan(
    query: str | None, scope: AlertScope | None = None
) -> MonitorQueryPlan | None:
    """The re-runnable form of the monitor's query, in the idiom it was written in."""
    if not query:
        return None
    metric = METRIC_QUERY.match(query)
    if metric:
        expression = metric.group("expr").strip()
        return MonitorQueryPlan(
            "timeseries", scope_expression(expression, scope) if scope else expression
        )
    event = EVENT_QUERY.match(query)
    if event:
        # The monitor embeds its event query as a quoted string, so the inner
        # quotes arrive escaped and Datadog rejects them on the way back in.
        return MonitorQueryPlan("events", event.group("expr").replace('\\"', '"'))
    log = LOG_QUERY.match(query)
    if log:
        return MonitorQueryPlan("logs", log.group("expr").replace('\\"', '"'))
    return None


@dataclass(frozen=True)
class Recipe:
    """The sweep for one alert class: which collectors, and which metrics."""

    collectors: tuple[Collector, ...]
    metrics: tuple[MetricSpec, ...] = ()


SWEEP = (
    Collector.MONITOR_QUERY,
    Collector.EVENTS_SERVICE,
    Collector.EVENTS_NAMESPACE,
    Collector.LOGS_AGGREGATE,
    Collector.LOGS_SAMPLE,
    Collector.METRICS,
    Collector.SPANS,
)
"""Every class sweeps the same shape; the classes differ in which metrics they ask for."""

TAG_FOR = {
    "cluster": "kube_cluster_name",
    "namespace": "kube_namespace",
    "stateful_set": "kube_stateful_set",
    "service": "service",
}


@dataclass(frozen=True)
class MetricSpec:
    """One metric, and the identifiers it must be narrowed by.

    Scoping is the whole content of this type. Measured on a live alert
    (2026-08-23, `grafana-observability-metrics` in `preprod-euw3`): asked for
    without the cluster, `sum:kubernetes_state.statefulset.replicas_ready` summed
    every cluster running a StatefulSet of that name and answered 7 ready of 8
    desired — 87% healthy — for a workload that was at 0 of 1. Scoped to the
    firing group it answers 1 → 0, which is the incident. A metric that is not
    narrowed to what alerted does not merely lack precision; it says the opposite
    of the truth, confidently.
    """

    query: str
    scope: tuple[str, ...]
    fallback: tuple[str, ...] = ("service",)
    """Used when the alert carried none of the preferred identifiers.

    The pod-down monitor groups ``by service`` and carries no cluster and no
    namespace, so the restart count — six restarts in the window, the strongest
    evidence a crash-restart diagnosis has — was being dropped entirely. Datadog
    tags these metrics with ``service:`` too, which is measurably true in this org
    and is the difference between a diagnosis and a shrug.
    """

    def render(self, values: dict[str, str | None]) -> str | None:
        for keys in (self.scope, self.fallback):
            tags = [f"{TAG_FOR[name]}:{values[name]}" for name in keys if values.get(name)]
            if tags:
                return f"{self.query}{{{','.join(tags)}}}"
        return None


WORKLOAD = ("cluster", "namespace", "stateful_set")
NAMESPACE = ("cluster", "namespace")

RESTARTS = MetricSpec("sum:kubernetes.containers.restarts", NAMESPACE)
REPLICAS = (
    MetricSpec("sum:kubernetes_state.statefulset.replicas_ready", WORKLOAD),
    MetricSpec("sum:kubernetes_state.statefulset.replicas_desired", WORKLOAD),
)
MEMORY = MetricSpec("avg:kubernetes.memory.usage_pct", NAMESPACE)
CPU = MetricSpec("avg:kubernetes.cpu.usage.total", NAMESPACE)

RECIPES: dict[AlertClass, Recipe] = {
    AlertClass.CRASH_RESTART: Recipe(SWEEP, (RESTARTS, *REPLICAS, MEMORY)),
    AlertClass.AVAILABILITY: Recipe(SWEEP, (RESTARTS, *REPLICAS)),
    AlertClass.SATURATION: Recipe(SWEEP, (MEMORY, CPU, RESTARTS)),
    AlertClass.LATENCY: Recipe(
        SWEEP, (MetricSpec("avg:trace.http.request.duration", ("service",)), CPU)
    ),
    AlertClass.ERROR_RATE: Recipe(
        SWEEP, (MetricSpec("sum:trace.http.request.errors", ("service",)), RESTARTS)
    ),
    AlertClass.GENERIC: Recipe(SWEEP, (RESTARTS,)),
}


def scope_values(scope: AlertScope) -> dict[str, str | None]:
    return {
        "cluster": scope.cluster,
        "namespace": scope.namespace,
        "service": scope.service,
        "stateful_set": scope.stateful_set or scope.service,
    }


def metric_queries(alert_class: AlertClass, scope: AlertScope) -> list[str]:
    """The recipe's metrics, narrowed to the identifiers this alert actually carried.

    A metric none of whose identifiers the alert carried is dropped rather than
    asked unscoped: an unscoped query answers a question about the whole org.
    """
    values = scope_values(scope)
    rendered = (spec.render(values) for spec in RECIPES[alert_class].metrics)
    return [query for query in rendered if query]


def scope_expression(expression: str, scope: AlertScope) -> str:
    """Narrow a monitor's own metric expression to the group that fired.

    A monitor is written across every group — ``{*} by {kube_cluster_name,…}`` —
    so re-running it verbatim answers for the whole org and the reduction then
    keeps whichever six series came first. Substituting the firing group's tags
    for ``{*}`` is what makes the re-run a fact about this incident.
    """
    values = scope_values(scope)
    tags = [f"{TAG_FOR[name]}:{values[name]}" for name in WORKLOAD if values.get(name)]
    return expression.replace("{*}", f"{{{','.join(tags)}}}") if tags else expression
