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


def monitor_query_plan(query: str | None) -> MonitorQueryPlan | None:
    """The re-runnable form of the monitor's query, in the idiom it was written in."""
    if not query:
        return None
    metric = METRIC_QUERY.match(query)
    if metric:
        return MonitorQueryPlan("timeseries", metric.group("expr").strip())
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
    metrics: tuple[str, ...] = ()


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

WORKLOAD_METRICS = (
    "sum:kubernetes.containers.restarts{{kube_namespace:{namespace}}}",
    "sum:kubernetes_state.statefulset.replicas_ready{{kube_stateful_set:{stateful_set}}}",
    "sum:kubernetes_state.statefulset.replicas_desired{{kube_stateful_set:{stateful_set}}}",
)

RECIPES: dict[AlertClass, Recipe] = {
    AlertClass.CRASH_RESTART: Recipe(
        SWEEP,
        (*WORKLOAD_METRICS, "avg:kubernetes.memory.usage_pct{{kube_namespace:{namespace}}}"),
    ),
    AlertClass.AVAILABILITY: Recipe(SWEEP, WORKLOAD_METRICS),
    AlertClass.SATURATION: Recipe(
        SWEEP,
        (
            "avg:kubernetes.memory.usage_pct{{kube_namespace:{namespace}}}",
            "avg:kubernetes.cpu.usage.total{{kube_namespace:{namespace}}}",
            *WORKLOAD_METRICS[:1],
        ),
    ),
    AlertClass.LATENCY: Recipe(
        SWEEP,
        (
            "avg:trace.http.request.duration{{service:{service}}}",
            "avg:kubernetes.cpu.usage.total{{kube_namespace:{namespace}}}",
        ),
    ),
    AlertClass.ERROR_RATE: Recipe(
        SWEEP,
        (
            "sum:trace.http.request.errors{{service:{service}}}",
            *WORKLOAD_METRICS[:1],
        ),
    ),
    AlertClass.GENERIC: Recipe(SWEEP, WORKLOAD_METRICS[:1]),
}


def metric_queries(alert_class: AlertClass, scope: AlertScope) -> list[str]:
    """The recipe's metrics, rendered for this scope.

    A metric whose scope the alert never carried is dropped rather than rendered
    with an empty tag value: ``{kube_stateful_set:}`` matches everything, which
    would answer a question about one workload with the whole cluster's numbers.
    """
    values = {
        "namespace": scope.namespace,
        "service": scope.service,
        "stateful_set": scope.stateful_set or scope.service,
    }
    rendered = []
    for template in RECIPES[alert_class].metrics:
        needed = re.findall(r"\{(\w+)\}", template)
        if any(not values.get(name) for name in needed):
            continue
        rendered.append(template.format(**{name: values[name] for name in needed}))
    return rendered
