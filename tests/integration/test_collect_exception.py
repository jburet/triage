"""The ``collect_exception`` node, through the deps it is given (M8 Phase 3).

Offline: the Datadog client is the recording fake replaying
``tests/fixtures/datadog/errors/otel_stacks_20260825/``, and no model tier is
called at all — F2 has nothing to classify, because the group already names its
exception type and its source location.
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
CAPTURE = Path(__file__).parent.parent / "fixtures" / "datadog" / "errors" / "otel_stacks_20260825"
SCANNER = "zeenea.service.api.ScannerUpsertItemException"


def retained_spans() -> dict:
    return json.loads((CAPTURE / "spans_plt-merck-qa.json").read_text())


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
    # The control query is a substring of the error-predicate one, so the
    # narrower shape is declared first and answers empty — as the org answers it.
    client = FakeDatadogClient(
        responses={
            "spans": {
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
    assert collection.exemplar is None
    statuses = {result.collector: result.status for result in collection.results}
    assert statuses[Collector.ERROR_SPANS] is CollectorStatus.SAMPLED_AWAY
    assert statuses[Collector.ERROR_LOGS] is CollectorStatus.SAMPLED_AWAY

    rendered = json.dumps(collection.as_payload(), default=str)
    assert "5,869 occurrences" in rendered
    assert "211,179 spans" in rendered
    assert "service:plt-systeme-u-rec status:error" in rendered
    assert 'exception.type:\\"zeenea.commons.exceptions.EntityNotFoundException\\"' in rendered


async def test_the_stack_reaches_the_payload_a_prompt_would_be_shown(config: Config):
    """The whole point of ADR-0029: a real occurrence, with the code it names."""
    client = FakeDatadogClient(responses={"spans_search": {"status:error": retained_spans()}})
    deps = build_deps(config, datadog=client)
    state = await collect_exception({"group": a_group(error_type=SCANNER)}, run_config(deps))

    collection = state["collection"]
    spans = next(r for r in collection.results if r.collector is Collector.ERROR_SPANS)
    assert spans.status is CollectorStatus.OK
    assert "ScannerService.scala:124" in spans.payload["stack"]

    assert collection.exemplar is not None
    rendered = json.dumps(collection.as_payload(), default=str)
    assert "Caused by: zeenea.commons.exceptions.TooBusyIndexingException" in rendered
    assert "zeenea/datacatalog/loadcontrol/LoadControl.scala:14" in rendered


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
