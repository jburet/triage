"""Classification, sweep, follow-up and qualification (M3 Phase 2, ADR-0016).

Run against the captured incident with a fake client that replays it, so what is
asserted is what Triage would have collected on 2026-08-22 at 00:47 UTC — the
alert that settled ADR-0016 by hand.
"""

from tests.conftest import (
    a_follow_up,
    a_qualification,
    a_service_entry,
    build_deps,
    captured_alert,
    fake_datadog,
    mapped,
    pod_down_alert,
    run_config,
)
from triage.config import Config
from triage.integrations.datadog import FakeDatadogClient
from triage.nodes.collect import classify_alert, collect, follow_up
from triage.nodes.qualify import qualify
from triage.runtime import Deps
from triage.schemas.alert import Alert
from triage.schemas.collection import (
    AlertClass,
    AlertClassification,
    Collection,
    Collector,
    CollectorStatus,
    FollowUpPlan,
    Qualification,
)
from triage.schemas.hypothesis import CauseType


async def swept(deps: Deps, alert: Alert | None = None) -> Collection:
    """classify → collect, the two nodes the sweep is made of."""
    state = {"alert": alert or pod_down_alert(), "service": "plt-hcl-software-uat"}
    state.update(await classify_alert(state, run_config(deps)))  # type: ignore[arg-type]
    state.update(await collect(state, run_config(deps)))  # type: ignore[arg-type]
    return state["collection"]  # type: ignore[return-value]


async def test_the_class_comes_from_the_model_and_the_window_from_the_monitor(config: Config):
    deps = build_deps(config, datadog=fake_datadog())

    result = await classify_alert({"alert": captured_alert()}, run_config(deps))

    assert result["classification"].alert_class is AlertClass.CRASH_RESTART
    assert deps.llm.calls_for(AlertClassification)[0].tier == "triage"
    window = result["window"]
    assert (window.end - window.start).total_seconds() == 40 * 60


async def test_an_alert_the_model_cannot_classify_still_gets_the_generic_sweep(config: Config):
    class Refuses:
        async def call(self, tier, prompt, schema):
            raise RuntimeError("the model returned nothing parsable")

    deps = build_deps(config, datadog=fake_datadog())
    result = await classify_alert(
        {"alert": captured_alert()}, run_config(Deps(**{**deps.__dict__, "llm": Refuses()}))
    )

    assert result["classification"].alert_class is AlertClass.GENERIC


async def test_the_monitor_is_read_from_the_event_and_not_from_the_api(config: Config):
    datadog = fake_datadog()
    deps = build_deps(config, datadog=datadog)

    collection = await swept(deps)

    assert datadog.queries_for("monitor") == []
    assert collection.results
    assert all(
        result.collector is not Collector.MONITOR_DEFINITION for result in collection.results
    )


async def test_a_monitor_the_event_did_not_carry_is_read_once(config: Config):
    datadog = fake_datadog()
    deps = build_deps(config, datadog=datadog)
    bare = captured_alert().model_copy(
        update={"monitor_query": None, "monitor_id": 76154596, "monitor_options": {}}
    )

    result = await classify_alert({"alert": bare}, run_config(deps))

    assert datadog.queries_for("monitor") == ["76154596"]
    assert result["alert"].monitor_query is not None


async def test_the_monitors_own_query_is_rerun_in_its_own_idiom(config: Config):
    deps = build_deps(config, datadog=fake_datadog())

    collection = await swept(deps)

    rerun = collection.by_collector(Collector.MONITOR_QUERY)[0]
    assert rerun.status is CollectorStatus.OK
    assert rerun.payload["count"] == 3
    assert "exit code 137" in " ".join(str(event["message"]) for event in rerun.payload["events"])


async def test_a_monitor_query_with_no_rerunnable_form_is_skipped_and_says_so(config: Config):
    deps = build_deps(config, datadog=fake_datadog())
    composite = pod_down_alert().model_copy(update={"monitor_query": "12345 || 67890"})

    collection = await swept(deps, composite)

    rerun = collection.by_collector(Collector.MONITOR_QUERY)[0]
    assert rerun.status is CollectorStatus.SKIPPED
    assert "no re-runnable form" in str(rerun.detail)


async def test_events_are_collected_at_both_scopes(config: Config):
    deps = build_deps(config, datadog=fake_datadog())

    collection = await swept(deps)

    def messages(collector: Collector) -> str:
        result = collection.by_collector(collector)[0]
        return " ".join(str(event["message"]) for event in result.payload["events"])

    assert "Liveness probe failed" not in messages(Collector.EVENTS_SERVICE)
    assert "Liveness probe failed" in messages(Collector.EVENTS_NAMESPACE)
    assert "exit code 137" in messages(Collector.EVENTS_NAMESPACE)


async def test_the_statefulset_deploy_event_reports_no_specification_change(config: Config):
    deps = build_deps(config, datadog=fake_datadog())

    collection = await swept(deps)

    events = collection.by_collector(Collector.EVENTS_NAMESPACE)[0].payload["events"]
    changes = [event["change"] for event in events if "change" in event]
    assert changes
    assert all(set(change["changed_fields"]) == {"ready_replicas"} for change in changes)
    assert all(change["verdict"].startswith("no specification change") for change in changes)


async def test_one_failing_collector_does_not_fail_the_collection(config: Config):
    datadog = fake_datadog()
    deps = build_deps(
        config,
        datadog=FakeDatadogClient(responses=datadog.responses, fail={"logs": "429 Too Many"}),
    )

    collection = await swept(deps)

    logs = collection.by_collector(Collector.LOGS_SAMPLE)[0]
    assert logs.status is CollectorStatus.FAILED
    assert "429" in str(logs.detail)
    assert collection.by_collector(Collector.EVENTS_NAMESPACE)[0].status is CollectorStatus.OK


async def test_a_signal_absent_everywhere_is_recorded_as_not_instrumented(config: Config):
    deps = build_deps(config, datadog=fake_datadog())

    collection = await swept(deps)

    spans = collection.by_collector(Collector.SPANS)[0]
    assert spans.status is CollectorStatus.NOT_INSTRUMENTED
    assert "7 days" in str(spans.detail)
    assert spans in collection.gaps


async def test_a_signal_absent_only_in_the_window_is_evidence_not_a_gap(config: Config):
    """The same empty response, the opposite meaning — decided by the widened query."""
    datadog = fake_datadog()
    responses = dict(datadog.responses)
    # Nothing for the incident scope, but the namespace has spans over seven days.
    responses["spans"] = {
        "kube_namespace:hcl-software-uat": {
            "data": [{"attributes": {"by": {"service": "other"}, "computes": {"c0": 42}}}]
        }
    }
    deps = build_deps(config, datadog=FakeDatadogClient(responses=responses))

    collection = await swept(deps)

    spans = collection.by_collector(Collector.SPANS)[0]
    assert spans.status is CollectorStatus.EMPTY
    assert "the absence is about this incident" in str(spans.detail)


async def test_the_collection_handed_on_stays_inside_the_prompt_budget(config: Config):
    tight = config.model_copy(
        update={"collection": config.collection.model_copy(update={"max_prompt_bytes": 4_000})}
    )
    deps = build_deps(tight, datadog=fake_datadog(), qualifications=[a_qualification()])
    collection = await swept(deps)

    await qualify(
        {"alert": pod_down_alert(), "collection": collection, "service": "plt-hcl-software-uat"},
        run_config(deps),
    )

    prompt = deps.llm.calls_for(Qualification)[0].prompt
    rendered = prompt.split("<collected>")[1].split("</collected>")[0]
    assert len(rendered.encode()) < 4_000
    assert "truncated to fit the prompt budget" in rendered


async def test_follow_up_calls_are_capped_and_the_refusal_is_recorded(config: Config):
    small = config.model_copy(
        update={"collection": config.collection.model_copy(update={"max_followup_calls": 1})}
    )
    request = {
        "collector": "events_namespace",
        "query": "kube_namespace:hcl-software-uat",
        "why": "The exit codes are only at namespace scope.",
    }
    deps = build_deps(small, datadog=fake_datadog(), follow_ups=[a_follow_up(request, request)])
    collection = await swept(deps)

    result = await follow_up(
        {"alert": pod_down_alert(), "collection": collection, "window": collection.window},
        run_config(deps),
    )

    assert result["collection"].followup_calls == 1
    assert result["followup_done"] is True
    assert "budget of 1 calls was already spent" in result["collection"].refused[0]


async def test_a_collector_triage_does_not_have_is_discarded_and_kept(config: Config):
    deps = build_deps(
        config,
        datadog=fake_datadog(),
        follow_ups=[
            a_follow_up(
                {
                    "collector": "grafana_dashboards",
                    "query": "anything",
                    "why": "It would be nice to have.",
                }
            )
        ],
    )
    collection = await swept(deps)

    result = await follow_up(
        {"alert": pod_down_alert(), "collection": collection, "window": collection.window},
        run_config(deps),
    )

    assert result["collection"].followup_calls == 0
    assert "'grafana_dashboards' is not a collector" in result["collection"].refused[0]


async def test_the_loop_stops_when_the_analysis_tier_says_it_has_enough(config: Config):
    deps = build_deps(config, datadog=fake_datadog(), follow_ups=[a_follow_up(done=True)])
    collection = await swept(deps)

    result = await follow_up(
        {"alert": pod_down_alert(), "collection": collection, "window": collection.window},
        run_config(deps),
    )

    assert result == {"followup_done": True}
    assert deps.llm.calls_for(FollowUpPlan)


async def test_qualify_resolves_the_deployed_commit_from_the_system_map(config: Config):
    deps = build_deps(
        config,
        repo=mapped(a_service_entry()),
        datadog=fake_datadog(),
        qualifications=[
            a_qualification(
                {
                    "cause_type": "app",
                    "service": "payments-api",
                    "description": "The idempotency cache is unbounded.",
                    "rank_score": 0.8,
                }
            )
        ],
    )
    collection = await swept(deps)

    result = await qualify(
        {"alert": pod_down_alert(), "collection": collection, "service": "payments-api"},
        run_config(deps),
    )

    hypothesis = result["hypotheses"][0]
    assert hypothesis.cause_type is CauseType.APP
    assert hypothesis.commit == "9f2c1ab"


async def test_an_unresolvable_commit_changes_the_cause_rather_than_inventing_one(config: Config):
    deps = build_deps(
        config,
        repo=mapped(a_service_entry("known-api", source_commit=None)),
        datadog=fake_datadog(),
        qualifications=[
            a_qualification(
                {
                    "cause_type": "app",
                    "service": "unmapped-api",
                    "description": "Something in a service nobody has mapped.",
                    "rank_score": 0.7,
                },
                {
                    "cause_type": "deployment",
                    "service": "known-api",
                    "description": "A release of a mapped service with no known commit.",
                    "rank_score": 0.6,
                },
            )
        ],
    )
    collection = await swept(deps)

    result = await qualify(
        {"alert": pod_down_alert(), "collection": collection, "service": "known-api"},
        run_config(deps),
    )

    unmapped, mapped_without_commit = result["hypotheses"]
    assert unmapped.cause_type is CauseType.DEPENDENCY
    assert unmapped.commit is None
    assert mapped_without_commit.cause_type is CauseType.INFRA
    assert mapped_without_commit.commit is None
