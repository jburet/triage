"""The alert poller: reading, routing, gating, launching (M3 Phase 4).

Built on the captured alert pair — one `error` event and the `ok` event that
closed the same cycle two minutes later — because that pair is the whole
argument for ADR-0018: it is exactly the kind of cycle the gate is there to
discard, and exactly the kind the flap rule is there not to lose.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import (
    a_service_entry,
    build_deps,
    captured,
    captured_alert,
    fake_datadog,
    mapped,
    run_config,
)
from triage.config import Config
from triage.db.repo import InMemoryRepository
from triage.integrations.datadog import FakeDatadogClient
from triage.integrations.platform import FakePlatformClient
from triage.nodes.poll import POLLER_NAME, poll_alerts
from triage.runtime import Deps
from triage.schemas.alert import Alert
from triage.schemas.signal import SignalStatus


@pytest.fixture
def config(jira_config: Config) -> Config:
    """This module exercises the Jira path, which the release configures off."""
    return jira_config


FIRED_AT = datetime(2026, 8, 22, 0, 47, 11, tzinfo=UTC)


def with_fresh_ids(page: dict, prefix: str) -> dict:
    """The same cycle again, as Datadog would send it: new event ids, same monitor and group."""
    for event in page["data"]:
        inner = event["attributes"]["attributes"]
        inner["evt"]["id"] = f"{prefix}-{inner['evt']['id']}"
    return page


def alert_events(*statuses: str) -> dict:
    """The captured monitor-alert events, filtered to the transitions a test needs."""
    events = [
        event
        for event in captured("events_kube_namespace")["data"]
        if Alert.is_monitor_alert(event)
        and event["attributes"]["attributes"].get("monitor-alert-event", {}).get("alert_type")
        in statuses
    ]
    return {"data": events}


def poller_deps(config: Config, page: dict | None = None, **overrides: object) -> Deps:
    """The poller's own event page, in front of the captured incident's responses.

    The capture is there because a launched run collects for real against these
    fakes: a poller test that stubbed the collection would not notice the gate
    handing the graph an alert it cannot sweep.
    """
    capture = fake_datadog().responses
    responses = {
        **capture,
        "events": {"source:alert": page or alert_events("error"), **capture["events"]},
    }
    client = FakeDatadogClient(responses=responses)
    return build_deps(config, datadog=client, **overrides)  # type: ignore[arg-type]


async def tick(deps: Deps, now: datetime | None = None) -> dict:
    return await poll_alerts({"now": now or FIRED_AT + timedelta(minutes=1)}, run_config(deps))


async def test_one_tick_stores_one_signal_per_cycle_and_advances_the_watermark(config: Config):
    repo = InMemoryRepository()
    deps = poller_deps(config, repo=repo)
    now = FIRED_AT + timedelta(minutes=1)

    first = await tick(deps, now)
    second = await tick(deps, now + timedelta(seconds=60))

    assert first["events_seen"] == 1
    assert len(first["created"]) == 1
    assert second.get("created") is None
    assert len(repo.signals) == 1
    signal = next(iter(repo.signals.values()))
    assert signal.status is SignalStatus.WAITING
    assert signal.external_id == captured_alert().event_id
    assert signal.monitor_id == 18369851
    assert repo.watermarks[POLLER_NAME] == now + timedelta(seconds=60)


async def test_each_tick_re_reads_the_overlap_and_deduplicates_on_the_event_id(config: Config):
    repo = InMemoryRepository()
    deps = poller_deps(config, repo=repo)
    now = FIRED_AT + timedelta(minutes=1)
    await tick(deps, now)

    await tick(deps, now + timedelta(seconds=60))

    queries = deps.datadog.calls
    assert len(queries) == 2
    assert len(repo.signals) == 1


async def test_an_alert_no_team_claims_is_recorded_and_says_nothing(config: Config):
    """No Slack message: an alert nobody configured is Triage's business, not a team's."""
    repo = InMemoryRepository()
    unclaimed = alert_events("error")
    unclaimed["data"][0]["attributes"]["attributes"]["monitor"]["groups"] = [
        "kube_cluster_name:prod-use1",
        "kube_namespace:someone-elses-thing",
        "kube_stateful_set:not-ours",
    ]
    narrow = config.model_copy(
        update={
            "teams": [
                team.model_copy(
                    update={
                        "service_patterns": ["payments-*"],
                        "namespace_patterns": ["payments*"],
                    }
                )
                for team in config.teams
            ]
        }
    )
    deps = poller_deps(narrow, unclaimed, repo=repo)

    result = await tick(deps)

    assert len(result["out_of_scope"]) == 1
    signal = next(iter(repo.signals.values()))
    assert signal.status is SignalStatus.OUT_OF_SCOPE
    assert deps.slack.messages == []


async def test_a_stateful_set_alert_with_no_service_tag_still_reaches_its_team(config: Config):
    repo = InMemoryRepository()
    deps = poller_deps(config, repo=repo)

    await tick(deps)

    signal = next(iter(repo.signals.values()))
    assert captured_alert().scope.service is None
    assert signal.team == "platform"
    assert signal.service == "plt-hcl-software-uat"


async def test_the_environment_comes_from_the_cluster_map_and_never_from_a_guess(config: Config):
    repo = InMemoryRepository()
    unmapped_cluster = alert_events("error")
    unmapped_cluster["data"][0]["attributes"]["attributes"]["monitor"]["groups"] = [
        "kube_cluster_name:sandbox-euw1",
        "kube_namespace:hcl-software-uat",
        "kube_stateful_set:plt-hcl-software-uat",
    ]
    deps = poller_deps(config, unmapped_cluster, repo=repo)

    result = await tick(deps)

    assert len(result["out_of_scope"]) == 1
    assert next(iter(repo.signals.values())).status is SignalStatus.OUT_OF_SCOPE


async def test_a_preprod_alert_is_out_of_scope_for_a_production_only_team(config: Config):
    repo = InMemoryRepository()
    preprod = alert_events("error")
    preprod["data"][0]["attributes"]["attributes"]["monitor"]["groups"] = [
        "kube_cluster_name:preprod-euw3",
        "kube_namespace:hcl-software-uat",
        "kube_stateful_set:plt-hcl-software-uat",
    ]
    deps = poller_deps(config, preprod, repo=repo)

    await tick(deps)

    signal = next(iter(repo.signals.values()))
    assert signal.status is SignalStatus.OUT_OF_SCOPE
    assert signal.team == "platform"


async def test_a_cycle_that_recovers_before_the_gate_is_never_analysed(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    deps = poller_deps(config, alert_events("error", "success"), repo=repo)

    result = await tick(deps, FIRED_AT + timedelta(minutes=3))

    signal = next(iter(repo.signals.values()))
    assert signal.status is SignalStatus.SELF_RECOVERED
    assert signal.duration_seconds == 120.0
    assert result.get("launched") is None
    assert deps.jira.created == []


async def test_a_cycle_still_firing_at_the_gate_is_launched(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    deps = poller_deps(config, repo=repo)

    await tick(deps, FIRED_AT + timedelta(minutes=1))
    result = await tick(deps, FIRED_AT + timedelta(minutes=16))

    assert len(result["launched"]) == 1
    assert repo.signals[result["launched"][0]].status is SignalStatus.TICKETED
    assert deps.jira.created


async def test_a_service_the_system_map_never_saw_is_told_about_not_analysed(config: Config):
    repo = InMemoryRepository()
    deps = poller_deps(config, repo=repo)

    await tick(deps, FIRED_AT + timedelta(minutes=1))
    result = await tick(deps, FIRED_AT + timedelta(minutes=16))

    assert result.get("launched") is None
    assert len(result["unmapped"]) == 1
    assert deps.slack.messages[0].channel == "#platform-alerts"
    assert "no cartography" in deps.slack.messages[0].text
    assert repo.signals[result["unmapped"][0]].status is SignalStatus.DISCARDED


async def test_enough_self_recovered_cycles_become_one_flapping_ticket(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    deps = poller_deps(config, alert_events("error", "success"), repo=repo)

    for index in range(config.thresholds.flap_count):
        page = with_fresh_ids(alert_events("error", "success"), f"cycle{index}")
        deps.datadog.responses["events"]["source:alert"] = page  # type: ignore[index]
        result = await tick(deps, FIRED_AT + timedelta(hours=index))

    assert result["flapping"] == [
        "18369851:kube_cluster_name:prod-use1,kube_namespace:hcl-software-uat,"
        "kube_stateful_set:plt-hcl-software-uat"
    ]
    assert len(deps.jira.created) == 1
    flapping = next(iter(repo.diagnoses.values()))
    assert "alert cycles" in flapping.symptom.description
    assert "the monitor is more sensitive" in str(flapping.probable_cause)
    assert all(signal.payload.get("flap_reported") for signal in repo.signals.values())


async def test_the_counter_resets_after_a_flapping_ticket(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    deps = poller_deps(config, repo=repo)

    for index in range(config.thresholds.flap_count + 1):
        page = with_fresh_ids(alert_events("error", "success"), f"cycle{index}")
        deps.datadog.responses["events"]["source:alert"] = page  # type: ignore[index]
        result = await tick(deps, FIRED_AT + timedelta(hours=index))

    assert result["flapping"] == []
    assert len(deps.jira.created) == 1


async def test_a_poller_that_was_down_replays_a_bounded_span_and_says_what_it_skipped(
    config: Config,
):
    repo = InMemoryRepository()
    deps = poller_deps(config, repo=repo)
    await repo.set_watermark(POLLER_NAME, FIRED_AT - timedelta(hours=3))

    result = await tick(deps, FIRED_AT)

    assert result["skipped_span"] is not None
    notice = deps.slack.messages[0]
    assert notice.channel == "#platform-alerts"
    assert "replayed only the last 30" in notice.text
    assert repo.watermarks[POLLER_NAME] == FIRED_AT


async def test_the_platform_runs_it_when_there_is_a_platform(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    platform = FakePlatformClient()
    deps = poller_deps(config, repo=repo, platform=platform)

    await tick(deps, FIRED_AT + timedelta(minutes=1))
    result = await tick(deps, FIRED_AT + timedelta(minutes=16))

    assert deps.jira.created == []
    assert [run.assistant_id for run in platform.runs] == ["incident"]
    assert platform.runs[0].thread_id == f"incident-{result['launched'][0]}"


async def test_a_re_notified_cycle_creates_one_run_and_not_one_per_tick(config: Config):
    """The Platform answers `create_run` immediately, so the poller must not ask twice.

    In-process, `run_incident` has moved the signal on by the time the tick ends;
    queued, it has not, and a signal left `waiting` is launched again sixty
    seconds later — one run per tick for as long as the alert keeps firing.
    """
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    platform = FakePlatformClient()
    deps = poller_deps(config, repo=repo, platform=platform)

    await tick(deps, FIRED_AT + timedelta(minutes=1))
    await tick(deps, FIRED_AT + timedelta(minutes=16))
    # Datadog re-notifies the same cycle: same monitor, same group, a new event id.
    deps.datadog.responses["events"]["source:alert"] = with_fresh_ids(  # type: ignore[index]
        alert_events("error"), "renotified"
    )
    result = await tick(deps, FIRED_AT + timedelta(minutes=17))

    assert len(repo.signals) == 1
    assert len(platform.runs) == 1
    assert result.get("launched") is None
    assert next(iter(repo.signals.values())).status is SignalStatus.ANALYSING
