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
from triage.config import CollectionConfig
from triage.schemas.collection import (
    AlertClass,
    Collection,
    Collector,
    CollectorResult,
    CollectorStatus,
)
from triage.schemas.common import TimeWindow

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


def test_a_metric_the_alerts_scope_cannot_fill_is_dropped_rather_than_left_open():
    scope = captured_alert().scope

    queries = metric_queries(AlertClass.CRASH_RESTART, scope)

    assert any("kube_namespace:hcl-software-uat" in query for query in queries)
    assert any("kube_stateful_set:plt-hcl-software-uat" in query for query in queries)
    assert all("{}" not in query and "kube_stateful_set:}" not in query for query in queries)
    assert metric_queries(AlertClass.LATENCY, scope.model_copy(update={"namespace": None})) == []


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
