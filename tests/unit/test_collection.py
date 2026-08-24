"""The collection layer, against the captured incident (M3 Phase 2, ADR-0016).

Every number asserted here was measured on
``tests/fixtures/datadog/hcl_software_uat_20260822/``, captured from the live org
on 2026-08-23. Logs and spans are retained about fifteen days, so that directory
is the only permanent record of these responses: it cannot be re-captured, which
is why the reduction is pinned against it rather than against a hand-written
payload that would drift towards whatever the code already does.
"""

from datetime import UTC, datetime, timedelta

from tests.conftest import captured, captured_alert, pod_down_alert
from triage.collect import reduce as reducers
from triage.collect.budget import fit
from triage.collect.recipes import (
    collection_window,
    evaluation_window,
    metric_queries,
    monitor_query_plan,
)
from triage.config import CollectionConfig, Config
from triage.schemas.alert import AlertScope
from triage.schemas.collection import (
    AlertClass,
    Collection,
    Collector,
    CollectorResult,
    CollectorStatus,
)
from triage.schemas.common import TimeWindow
from triage.scope import declared_environment, resolve

CAPS = CollectionConfig()


def test_the_window_is_the_monitors_own_evaluation_window_times_the_multiplier():
    alert = captured_alert()
    assert evaluation_window(alert.monitor_query) == timedelta(minutes=5)

    window = collection_window(alert, CAPS, now=alert.fired_at + timedelta(hours=1))

    assert window.start == alert.fired_at - timedelta(minutes=20)
    assert window.end == alert.fired_at + timedelta(minutes=20)


def test_the_window_is_clamped_at_both_ends_and_never_runs_into_the_future():
    alert = captured_alert()
    slow = alert.model_copy(update={"monitor_query": "avg(last_4h):sum:x{*} > 1"})
    fast = alert.model_copy(update={"monitor_query": "avg(last_1m):sum:x{*} > 1"})

    slow_window = collection_window(slow, CAPS, now=alert.fired_at + timedelta(days=1))
    assert slow_window.start == alert.fired_at - timedelta(hours=CAPS.window_max_hours)
    quick = collection_window(fast, CAPS, now=alert.fired_at + timedelta(hours=1))
    assert quick.start == alert.fired_at - timedelta(minutes=CAPS.window_min_minutes)

    during = collection_window(fast, CAPS, now=alert.fired_at + timedelta(minutes=2))
    assert during.end == alert.fired_at + timedelta(minutes=2)


def test_a_monitor_query_is_replanned_in_the_idiom_it_was_written_in():
    """A metric monitor's expression sent to event search returns 400 — read as no data."""
    metric = monitor_query_plan(captured_alert().monitor_query)
    events = monitor_query_plan(pod_down_alert().monitor_query)

    assert metric is not None
    assert metric.idiom == "timeseries"
    assert metric.query.startswith("sum:kubernetes_state.statefulset.replicas_ready")
    assert events is not None
    assert events.idiom == "events"
    assert '@event_object:"containerd:/tasks/delete"' in events.query
    assert monitor_query_plan("composite: 123 && 456") is None


def test_every_metric_is_narrowed_to_the_group_that_fired():
    """Measured on a live alert: unscoped, the replica count answered for every cluster."""
    scope = captured_alert().scope

    queries = metric_queries(AlertClass.CRASH_RESTART, scope)

    replicas = next(query for query in queries if "replicas_ready" in query)
    assert replicas == (
        "sum:kubernetes_state.statefulset.replicas_ready{"
        "kube_cluster_name:prod-use1,kube_namespace:hcl-software-uat,"
        "kube_stateful_set:plt-hcl-software-uat}"
    )
    assert all("kube_cluster_name:prod-use1" in query for query in queries)


def test_a_metric_none_of_whose_identifiers_the_alert_carried_is_dropped():
    assert metric_queries(AlertClass.CRASH_RESTART, AlertScope()) == []


def test_an_alert_with_only_a_service_falls_back_to_scoping_by_service():
    """The pod-down monitor groups by service alone; without this its restarts vanish."""
    queries = metric_queries(AlertClass.CRASH_RESTART, AlertScope(service="plt-tenant"))

    assert "sum:kubernetes.containers.restarts{service:plt-tenant}" in queries
    assert "avg:kubernetes.memory.usage_pct{service:plt-tenant}" in queries


def test_the_monitors_own_expression_is_narrowed_to_the_group_that_fired():
    """`{*} by {…}` re-run verbatim answers for the whole org, not for this incident."""
    alert = captured_alert()

    plan = monitor_query_plan(alert.monitor_query, alert.scope)

    assert plan is not None
    assert "{*}" not in plan.query
    assert (
        plan.query.count(
            "kube_cluster_name:prod-use1,kube_namespace:hcl-software-uat,"
            "kube_stateful_set:plt-hcl-software-uat"
        )
        == 2
    )


def test_namespace_scope_carries_the_exit_code_the_service_scope_never_saw():
    service = reducers.reduce_events(captured("events_service"), CAPS.max_events)
    namespace = reducers.reduce_events(captured("events_kube_namespace"), CAPS.max_events)

    def messages(reduced: dict) -> str:
        return " ".join(str(event["message"]) for event in reduced["events"])

    assert "Liveness probe failed" not in messages(service)
    assert "Liveness probe failed" in messages(namespace)
    assert "exit code 137" in messages(namespace)


def test_a_kubernetes_change_event_is_read_as_a_diff_and_not_as_its_title():
    reduced = reducers.reduce_events(captured("events_kube_namespace"), CAPS.max_events)

    changes = [event for event in reduced["events"] if "change" in event]
    assert len(changes) == 2
    for change in changes:
        assert set(change["change"]["changed_fields"]) == {"ready_replicas"}
        assert change["change"]["verdict"].startswith("no specification change")


def test_sixty_log_entries_reduce_to_templates_with_counts_and_a_capped_sample():
    payload = captured("logs_at_alert")

    reduced = reducers.reduce_logs(payload, CAPS.max_log_templates, CAPS.max_log_lines)

    # Eleven by hand (the M3 plan's count); thirteen by the normaliser, which keeps
    # the three `register_scanner` variants apart because their platform string
    # arrives outside the `key=value` shape it collapses.
    assert reduced["count"] == 60
    assert reduced["distinct_templates"] == 13
    assert len(reduced["templates"]) <= CAPS.max_log_templates
    assert reduced["templates"][0] == {
        "status": "warn",
        "template": "platform api authentication failed",
        "count": 45,
    }
    assert len(reduced["lines"]) <= CAPS.max_log_lines


def test_a_timeseries_is_downsampled_and_summarised():
    reduced = reducers.reduce_timeseries(
        captured("metric_kubernetes_state_statefulset_replicas_ready"), 6, 20
    )

    series = reduced["series"][0]
    assert len(series["series"]) <= 20
    assert series["points"] > 20
    assert series["min"] == 0.0
    assert series["max"] >= 1.0


def test_an_oversized_collection_is_cut_to_the_budget_and_says_where():
    events = reducers.reduce_events(captured("events_kube_namespace"), CAPS.max_events)
    logs = reducers.reduce_logs(captured("logs_at_alert"), 15, 25)
    window = TimeWindow(
        start=datetime(2026, 8, 22, tzinfo=UTC), end=datetime(2026, 8, 23, tzinfo=UTC)
    )
    collection = Collection(
        alert_class=AlertClass.CRASH_RESTART,
        window=window,
        results=[
            CollectorResult(
                collector=Collector.EVENTS_NAMESPACE,
                query="kube_namespace:hcl-software-uat",
                status=CollectorStatus.OK,
                payload=events,
            ),
            CollectorResult(
                collector=Collector.LOGS_SAMPLE,
                query="service:plt-hcl-software-uat",
                status=CollectorStatus.OK,
                payload=logs,
            ),
        ],
    )

    fitted = fit(collection, 8_000)

    assert len(str(fitted.as_payload()).encode()) < len(str(collection.as_payload()).encode())
    truncated = [result for result in fitted.results if result.truncated]
    assert truncated
    assert all("truncated to fit the prompt budget" in str(result.detail) for result in truncated)


def test_the_environment_can_come_from_the_monitors_own_env_filter(config: Config):
    """A monitor grouped only `by service` carries no cluster; its query still says prod."""
    alert = pod_down_alert().model_copy(
        update={"scope": AlertScope(service="plt-hcl-software-uat")}
    )

    routing = resolve(config, alert)

    assert declared_environment(alert) == "prod"
    assert routing.in_scope
    assert routing.environment == "prod"
    assert "from the monitor's own env: filter" in routing.reason


def test_a_monitor_that_declares_no_environment_stays_out_of_scope(config: Config):
    alert = pod_down_alert().model_copy(
        update={
            "scope": AlertScope(service="plt-hcl-software-uat"),
            "monitor_query": 'events("service:plt-*").rollup("count").last("5m") > 0',
        }
    )

    routing = resolve(config, alert)

    assert not routing.in_scope
    assert "cannot be determined" in routing.reason


def test_a_memory_alert_collects_the_limit_it_is_a_percentage_of():
    """99.99% of what? Without the limit the diagnosis cannot say, and guessed.

    On the plt-merck incident (2026-08-24) the only memory signal collected was
    `usage_pct`. The analysis reached for a Helm chart to find the denominator,
    read one that does not deploy this workload, and put 6Gi in the ticket; the
    limit Datadog reports for that tenant is 5Gi. The number is telemetry, so it
    is collected rather than inferred.
    """
    queries = metric_queries(AlertClass.CRASH_RESTART, AlertScope(service="plt-merck"))

    assert "avg:kubernetes.memory.limits{service:plt-merck}" in queries
    assert "avg:kubernetes.memory.requests{service:plt-merck}" in queries


def test_a_memory_alert_collects_the_working_set_the_kernel_kills_on():
    """`usage` counts page cache; the OOM killer counts the working set.

    Measured on plt-merck at the crash: usage_pct 99.99%, working_set 88% of the
    limit, rss 67%. Reporting only the first overstates the pressure and makes a
    reclaimable cache look like an exhausted heap.
    """
    queries = metric_queries(AlertClass.SATURATION, AlertScope(service="plt-merck"))

    assert "avg:kubernetes.memory.working_set{service:plt-merck}" in queries
