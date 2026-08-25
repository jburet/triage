"""The ``collect_exception`` node, through the deps it is given (M8 Phase 3).

Offline: the Datadog client is the recording fake, and no model tier is called at
all — F2 has nothing to classify, because the group already names its exception
type and its source location.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.conftest import build_deps, run_config
from triage.config import Config
from triage.integrations.datadog import FakeDatadogClient
from triage.nodes.collect_exception import collect_exception
from triage.schemas.collection import Collector, CollectorStatus
from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorGroup, ErrorTrack, Novelty

NOW = datetime(2026, 8, 25, 5, 35, 24, tzinfo=UTC)
SYNTHETIC = (
    Path(__file__).parent.parent
    / "fixtures"
    / "datadog"
    / "errors"
    / "synthetic_stack"
    / "logs_error_sample.json"
)


def a_group(**overrides: object) -> ErrorGroup:
    base: dict[str, object] = {
        "key": "EntityNotFoundException|OdbClient.scala|$anonfun$load$6|platform",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "repository": "platform",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"plt-systeme-u-rec": 5869},
        "occurrences": 5869,
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


async def test_the_node_collects_over_a_window_it_derives_from_the_tick(config: Config):
    deps = build_deps(config, datadog=FakeDatadogClient())
    state = await collect_exception({"group": a_group()}, run_config(deps))

    window: TimeWindow = state["window"]
    lookback = timedelta(minutes=config.errors.lookback_minutes)
    assert window.end - window.start == lookback
    assert state["collection"].window == window
    assert state["collection"].group_key == a_group().key


async def test_a_group_whose_evidence_was_discarded_reports_the_discard(config: Config):
    """The measured case, end to end: the report says why there is nothing to read."""
    # The control query is a substring of the other two, so the error-predicate
    # shapes are declared first and answer empty — as the org answers them.
    client = FakeDatadogClient(
        responses={
            "spans": {
                "@error.type": {"data": []},
                "status:error": {"data": []},
                "service:plt-systeme-u-rec": _aggregate("plt-systeme-u-rec", 211179),
            },
            "logs_aggregate": {"service:plt-systeme-u-rec": _log_aggregate(11)},
        }
    )
    deps = build_deps(config, datadog=client)
    state = await collect_exception({"group": a_group()}, run_config(deps))

    collection = state["collection"]
    assert collection.under_matched
    statuses = {result.collector: result.status for result in collection.results}
    assert statuses[Collector.ERROR_SPANS] is CollectorStatus.SAMPLED_AWAY
    assert statuses[Collector.ERROR_LOGS] is CollectorStatus.SAMPLED_AWAY

    payload = collection.as_payload()
    rendered = json.dumps(payload, default=str)
    assert "5,869 occurrences" in rendered
    assert "211,179 spans" in rendered
    assert '@error.type:\\"zeenea.commons.exceptions.EntityNotFoundException\\"' in rendered


async def test_the_stack_reaches_the_payload_a_prompt_would_be_shown(config: Config):
    client = FakeDatadogClient(
        responses={"logs": {"@error.type": json.loads(SYNTHETIC.read_text())}}
    )
    deps = build_deps(config, datadog=client)
    state = await collect_exception({"group": a_group()}, run_config(deps))

    logs = next(
        result for result in state["collection"].results if result.collector is Collector.ERROR_LOGS
    )
    assert logs.status is CollectorStatus.OK
    assert "OdbClient.scala:412" in logs.payload["stack"]


async def test_a_refused_call_does_not_stop_the_others(config: Config):
    client = FakeDatadogClient(fail={"spans_search": "429 too many requests"})
    deps = build_deps(config, datadog=client)
    state = await collect_exception({"group": a_group()}, run_config(deps))

    results = {result.collector: result for result in state["collection"].results}
    assert results[Collector.ERROR_SPANS].status is CollectorStatus.FAILED
    assert "429" in (results[Collector.ERROR_SPANS].detail or "")
    assert results[Collector.ERROR_LOGS].status is not CollectorStatus.FAILED


def _aggregate(service: str, count: int) -> dict:
    return {"data": [{"attributes": {"by": {"service": service}, "computes": {"c0": count}}}]}


def _log_aggregate(count: int) -> dict:
    return {"data": {"buckets": [{"by": {}, "computes": {"c0": count}}]}}
