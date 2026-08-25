"""F2's collection rules (M8 Phase 3, ADR-0029).

Every payload here is `tests/fixtures/datadog/errors/otel_stacks_20260825/`, captured
live on 2026-08-25, and every number asserted was measured on it. The short version:
the exception, its message and its whole stack are inside the span attribute
`custom.events`, so the join is `exception.type` parsed out of a `status:error` search
rather than the `@error.type` attribute, which is empty under OpenTelemetry and matched
nothing. Evidence is often present, and often is not this defect's — what these tests
pin is that the four outcomes are told apart.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from triage.collect.budget import fit
from triage.config import CollectionConfig
from triage.errors.sweep import (
    collect_group,
    collection_window,
    exemplar_of,
    reconstruct,
    reduce_error_logs,
    reduce_error_spans,
    stack_of,
)
from triage.integrations.datadog import FakeDatadogClient
from triage.schemas.collection import Collector, CollectorResult, CollectorStatus
from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorCollection, ErrorGroup, ErrorTrack, Novelty

CAPS = CollectionConfig()
NOW = datetime(2026, 8, 25, 5, 35, 24, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW - timedelta(hours=1), end=NOW)

CAPTURE = Path(__file__).parent.parent / "fixtures" / "datadog" / "errors" / "otel_stacks_20260825"

SCANNER = "zeenea.service.api.ScannerUpsertItemException"
NOT_FOUND = "zeenea.commons.exceptions.EntityNotFoundException"


def spans(service: str = "plt-merck-qa") -> dict[str, Any]:
    """Twenty retained error spans, twenty complete OTel stacks, all of one type."""
    return json.loads((CAPTURE / f"spans_{service}.json").read_text())


def a_group(**overrides: object) -> ErrorGroup:
    """The captured defect: `ScannerUpsertItemException` in three `plt-merck*` tenants."""
    base: dict[str, object] = {
        "key": f"{SCANNER}|zeenea.service.api.ScannerService.scala|$anonfun$applyOrElse$3|platform",
        "error_type": SCANNER,
        "file_path": "zeenea.service.api.ScannerService.scala",
        "function_name": "$anonfun$applyOrElse$3",
        "repository": "platform",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"plt-merck-dev": 1271, "plt-merck-qa": 942},
        "occurrences": 2213,
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


def carrying(payload: dict[str, Any]) -> FakeDatadogClient:
    return FakeDatadogClient(responses={"spans_search": {"status:error": payload}})


# -- 3.3 the reconstruction, stated verbatim ------------------------------------


def test_the_query_is_the_broad_one_and_the_join_is_the_otel_exception_type():
    queries = reconstruct(a_group())
    assert queries.query == "service:(plt-merck-dev OR plt-merck-qa) status:error"
    assert queries.match == (f'exception.type:"{SCANNER}" inside each span\'s custom.events')
    assert queries.control == "service:(plt-merck-dev OR plt-merck-qa)"


def test_one_service_is_not_wrapped_in_an_or():
    assert reconstruct(a_group(services={"plt-merck": 4})).query == "service:plt-merck status:error"


async def test_the_collection_states_the_query_and_the_match_verbatim():
    collection = await collect_group(FakeDatadogClient(), a_group(), WINDOW, CAPS)
    payload = collection.as_payload()
    assert payload["reconstructed_query"]["query"] == reconstruct(a_group()).query
    assert payload["reconstructed_query"]["match"] == reconstruct(a_group()).match
    assert "matched inside each span's OpenTelemetry events" in payload["reconstruction_caveat"]
    assert payload["claimed_occurrences"] == 2213
    assert collection.under_matched


# -- 3.6 the join finds the occurrences -----------------------------------------


async def test_a_retained_span_carrying_this_exception_is_evidence_with_its_stack():
    collection = await collect_group(carrying(spans()), a_group(), WINDOW, CAPS)
    result = next(r for r in collection.results if r.collector is Collector.ERROR_SPANS)
    assert result.status is CollectorStatus.OK
    assert result.query == "service:(plt-merck-dev OR plt-merck-qa) status:error"
    assert result.payload["count"] == 20
    assert result.payload["lines"][0]["trace_id"] == "3a83b9a36ce25524334abbbb39f0072a"
    assert "20 of the 20 error spans" in (result.detail or "")

    exemplar = collection.exemplar
    assert exemplar is not None
    assert exemplar.error_type == SCANNER
    assert "Caused by: zeenea.commons.exceptions.TooBusyIndexingException" in exemplar.stack
    assert exemplar.frames[0] == "zeenea/service/api/ScannerService.scala:124"
    assert "zeenea/datacatalog/loadcontrol/LoadControl.scala:14" in exemplar.frames
    assert exemplar.operation == "grpc.server.request"


def test_a_span_of_another_exception_is_counted_and_named_but_never_matched():
    reduced = reduce_error_spans(spans(), CAPS.max_log_lines, NOT_FOUND)
    assert reduced["count"] == 0
    assert reduced["lines"] == []
    assert reduced["retained_error_spans"] == 20
    assert reduced["other_exception_types"] == {SCANNER: 20}


def test_no_exemplar_is_invented_when_nothing_carries_the_type():
    assert exemplar_of(spans(), NOT_FOUND) is None
    assert exemplar_of({}, SCANNER) is None


# -- 3.1 the reduction must not eat the stack -----------------------------------


def real_stack() -> str:
    return exemplar_of(spans(), SCANNER).stack  # type: ignore[union-attr]


def a_log_page(stack: str) -> dict[str, Any]:
    """Nine log events, two message shapes, exactly one carrying the real stack.

    Hand-built, and it has to be: the org ships no error logs at all for these
    services, so there is no page of them to capture. The stack inside it is the
    captured one — the rule under test is that the template reduction finds it
    wherever it is and returns it whole, not that it happens to keep the newest line.
    """
    events = []
    for index in range(9):
        message = (
            f"Error in query «load_contact_by_id» with (id -> contact:{index}) : Not found"
            if index < 6
            else f"Timed out waiting for the index lock after {index}000 ms"
        )
        attributes: dict[str, Any] = {"message": message, "status": "error"}
        if index == 4:
            attributes["error"] = {"stack": stack}
        events.append({"attributes": attributes})
    return {"data": events}


def test_the_stack_survives_a_reduction_that_collapses_everything_else():
    stack = real_stack()
    reduced = reduce_error_logs(a_log_page(stack), CAPS.max_log_templates, CAPS.max_log_lines)
    assert reduced["count"] == 9
    assert reduced["distinct_templates"] == 2
    assert reduced["stack"] == stack
    assert "…" not in reduced["stack"]


def test_the_stack_is_found_wherever_the_shipper_put_it():
    assert stack_of({"error": {"stack": "boom\n\tat X"}}) == "boom\n\tat X"
    assert stack_of({"attributes": {"error": {"stack_trace": "deep"}}}) == "deep"
    assert stack_of({"message": "NPE\n\tat a.b.C(C.java:1)"}) == "NPE\n\tat a.b.C(C.java:1)"
    assert stack_of({"message": "just a line"}) is None


def test_the_prompt_budget_cuts_spans_before_it_cuts_the_stack():
    reduced = reduce_error_spans(spans(), CAPS.max_log_lines, SCANNER)
    collection = ErrorCollection(
        group_key="k",
        window=WINDOW,
        reconstruction=reconstruct(a_group()),
        claimed_occurrences=2213,
        results=[_result(Collector.ERROR_SPANS, CollectorStatus.OK, reduced)],
    )
    kept = fit(collection, 3_000).results[0]
    assert kept.truncated
    assert len(kept.payload["lines"]) < len(reduced["lines"])
    assert kept.payload["stack"] == reduced["stack"]
    assert "fewer lines" in (kept.detail or "")


def _result(collector: Collector, status: CollectorStatus, payload: dict) -> CollectorResult:
    return CollectorResult(collector=collector, query="q", status=status, payload=payload)


def test_a_span_is_reduced_to_what_opens_the_trace():
    reduced = reduce_error_spans(spans(), 10, SCANNER)
    line = reduced["lines"][0]
    assert line["trace_id"] == "3a83b9a36ce25524334abbbb39f0072a"
    assert line["resource"] == "UpsertItem zeenea.scanner.api.grpc.ScannerService"
    assert line["error_type"] == SCANNER
    assert line["error_message"].startswith("TooBusyIndexingException on item upsert")


# -- 3.4 the window -------------------------------------------------------------


def test_the_window_runs_back_from_the_tick_to_the_configured_lookback():
    window = collection_window(NOW, 60)
    assert window.end == NOW
    assert window.start == NOW - timedelta(minutes=60)


def test_the_window_ignores_how_long_ago_the_defect_was_first_seen():
    """A group first seen in March asks for an hour, not five months."""
    assert collection_window(NOW, 60).start > a_group().first_seen


def test_the_tick_the_count_was_taken_over_is_the_window_the_evidence_is_sought_in():
    """Measured live: a 13-hour backfill counted a burst at 02:29 and then looked for
    it between 08:41 and 09:41, where it was not. The window has to be the one the
    occurrences were counted over, or the join has nothing to join."""
    counted = TimeWindow(start=NOW - timedelta(hours=13), end=NOW)
    assert collection_window(NOW, 60, counted) == counted


def test_a_tick_that_carries_no_window_still_falls_back_to_the_lookback():
    assert collection_window(NOW, 60, None).start == NOW - timedelta(minutes=60)


# -- 3.2 / 3.5 what an empty collector means ------------------------------------


async def test_a_service_nobody_collects_is_not_instrumented():
    collection = await collect_group(FakeDatadogClient(), a_group(), WINDOW, CAPS)
    statuses = {result.collector: result.status for result in collection.results}
    assert statuses[Collector.ERROR_SPANS] is CollectorStatus.NOT_INSTRUMENTED
    assert statuses[Collector.ERROR_LOGS] is CollectorStatus.NOT_INSTRUMENTED
    assert "nothing at all for these services either" in (collection.results[0].detail or "")


async def test_error_spans_that_are_not_this_defect_are_a_stated_absence_not_a_gap():
    """The measured case: 20 retained error spans for a service, none of this type.

    This is what ADR-0027 got wrong. Nothing was matching them against the group
    because the matching attribute was wrong, and the report called them
    "unrelated noise". They are not noise: they are the proof that the sampler is
    keeping error spans for this service and threw *this* defect's away.
    """
    collection = await collect_group(carrying(spans()), a_group(error_type=NOT_FOUND), WINDOW, CAPS)
    result = next(r for r in collection.results if r.collector is Collector.ERROR_SPANS)
    assert result.status is CollectorStatus.SAMPLED_AWAY
    assert "20 retained error spans and none of them carries this exception" in (
        result.detail or ""
    )
    assert f"`{SCANNER}` 20" in (result.detail or "")
    assert "2,213 occurrences" in (result.detail or "")
    assert collection.exemplar is None


async def test_a_service_whose_whole_error_track_is_discarded_is_sampled_away_too():
    """No error span at all, and 211,179 spans with the error predicate dropped."""
    client = FakeDatadogClient(
        responses={
            "spans": {
                "status:error": {"data": []},
                "service:(plt-merck-dev OR plt-merck-qa)": {
                    "data": [
                        {
                            "attributes": {
                                "by": {"service": "plt-merck-qa"},
                                "computes": {"c0": 211179},
                            }
                        }
                    ]
                },
            }
        }
    )
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    result = next(r for r in collection.results if r.collector is Collector.ERROR_SPANS)
    assert result.status is CollectorStatus.SAMPLED_AWAY
    assert "no error spans were retained for these services at all" in (result.detail or "")
    assert "211,179 spans" in (result.detail or "")
    assert "only a retention filter can bring it back" in (result.detail or "")


async def test_the_span_counts_collector_says_it_counts_more_than_this_defect():
    client = FakeDatadogClient(
        responses={
            "spans": {
                "status:error": {
                    "data": [
                        {
                            "attributes": {
                                "by": {"resource_name": "UpsertItem"},
                                "computes": {"c0": 9},
                            }
                        }
                    ]
                }
            }
        }
    )
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    counts = next(r for r in collection.results if r.collector is Collector.ERROR_SPAN_COUNTS)
    assert counts.status is CollectorStatus.OK
    assert "not only this exception" in (counts.detail or "")


async def test_a_collector_datadog_refuses_is_a_stated_failure_and_the_run_continues():
    client = FakeDatadogClient(
        fail={"logs": "POST /api/v2/logs/events/search returned 429: too many requests"},
        responses={"spans_search": {"status:error": spans()}},
    )
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    logs = next(r for r in collection.results if r.collector is Collector.ERROR_LOGS)
    assert logs.status is CollectorStatus.FAILED
    assert "429" in (logs.detail or "")
    assert len(collection.results) == 3
    assert any(result.status is CollectorStatus.OK for result in collection.results)


async def test_a_control_that_cannot_be_run_leaves_the_absence_unexplained_rather_than_guessed():
    client = FakeDatadogClient(fail={"logs_aggregate": "500 internal error"})
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    logs = next(r for r in collection.results if r.collector is Collector.ERROR_LOGS)
    assert logs.status is CollectorStatus.EMPTY
    assert "the control query could not be run" in (logs.detail or "")


async def test_the_control_is_asked_once_per_track_not_once_per_collector():
    client = FakeDatadogClient()
    await collect_group(client, a_group(), WINDOW, CAPS)
    control = reconstruct(a_group()).control
    assert client.queries_for("spans").count(control) == 1
