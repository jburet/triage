"""F2's collection rules (M8 Phase 3, ADR-0027).

Every number about the org here was measured on 2026-08-25 by hand-run probes
against the live account, and is recorded in ADR-0027 and in
``tests/fixtures/datadog/errors/synthetic_stack/NOTES.md``. The short version:
the services Error Tracking raises issues for retain no error spans and ship no
error logs, so the reconstruction under-matches by construction, and what these
tests pin is that it *says so* rather than that it succeeds.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from triage.collect.budget import fit
from triage.config import CollectionConfig
from triage.errors.sweep import (
    collect_group,
    collection_window,
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

SYNTHETIC = Path(__file__).parent.parent / "fixtures" / "datadog" / "errors" / "synthetic_stack"


def synthetic_logs() -> dict:
    return json.loads((SYNTHETIC / "logs_error_sample.json").read_text())


def a_group(**overrides: object) -> ErrorGroup:
    """Built from the reference hour's two largest issues: 6,344 and 5,869 occurrences.

    Both are ``EntityNotFoundException`` at ``OdbClient.scala:$anonfun$load$6``, and
    both returned zero spans and zero logs to a query rebuilt from their own fields.
    """
    base: dict[str, object] = {
        "key": "zeenea.commons.exceptions.EntityNotFoundException|"
        "zeenea.repository.orientdb.OdbClient.scala|$anonfun$load$6|platform",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "repository": "platform",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"plt-systeme-u": 6344, "plt-systeme-u-rec": 5869},
        "occurrences": 12213,
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


# -- 3.3 the reconstruction, stated verbatim ------------------------------------


def test_the_query_is_rebuilt_from_the_groups_own_fields_and_nothing_else():
    queries = reconstruct(a_group())
    assert queries.narrow == (
        "service:(plt-systeme-u OR plt-systeme-u-rec) "
        '@error.type:"zeenea.commons.exceptions.EntityNotFoundException"'
    )
    assert queries.broad == "service:(plt-systeme-u OR plt-systeme-u-rec) status:error"
    assert queries.control == "service:(plt-systeme-u OR plt-systeme-u-rec)"


def test_one_service_is_not_wrapped_in_an_or():
    queries = reconstruct(a_group(services={"plt-merck": 4}))
    assert queries.narrow.startswith("service:plt-merck @error.type:")


async def test_the_collection_states_the_reconstructed_query_verbatim():
    collection = await collect_group(FakeDatadogClient(), a_group(), WINDOW, CAPS)
    payload = collection.as_payload()
    assert payload["reconstructed_queries"]["narrow"] == reconstruct(a_group()).narrow
    assert "rebuilt from the group's own fields" in payload["reconstruction_caveat"]
    # The claim is half the finding: zero spans is a shrug, zero spans against
    # 12,213 counted occurrences is a statement about the pipeline.
    assert payload["claimed_occurrences"] == 12213
    assert collection.under_matched


# -- 3.1 the reduction must not eat the stack -----------------------------------


def test_the_stack_survives_a_reduction_that_collapses_everything_else():
    reduced = reduce_error_logs(synthetic_logs(), CAPS.max_log_templates, CAPS.max_log_lines)
    # Nine lines, two templates: the reduction is doing its job on the messages.
    assert reduced["count"] == 9
    assert reduced["distinct_templates"] == 2
    stack = reduced["stack"]
    assert stack.startswith("zeenea.commons.exceptions.EntityNotFoundException:")
    assert "OdbClient.scala:412" in stack
    assert stack.count("\n\tat ") == 9
    assert "…" not in stack


def test_the_stack_is_found_wherever_the_shipper_put_it():
    assert stack_of({"error": {"stack": "boom\n\tat X"}}) == "boom\n\tat X"
    assert stack_of({"attributes": {"error": {"stack_trace": "deep"}}}) == "deep"
    # Nothing marks it but the shape of the message itself.
    assert stack_of({"message": "NPE\n\tat a.b.C(C.java:1)"}) == "NPE\n\tat a.b.C(C.java:1)"
    assert stack_of({"message": "just a line"}) is None


def test_the_prompt_budget_cuts_lines_before_it_cuts_the_stack():
    reduced = reduce_error_logs(synthetic_logs(), CAPS.max_log_templates, CAPS.max_log_lines)
    collection = ErrorCollection(
        group_key="k",
        window=WINDOW,
        reconstruction=reconstruct(a_group()),
        claimed_occurrences=12213,
        results=[
            _result(Collector.ERROR_LOGS, CollectorStatus.OK, reduced),
        ],
    )
    fitted = fit(collection, 1_500)
    kept = fitted.results[0]
    assert kept.truncated
    assert len(kept.payload["lines"]) < len(reduced["lines"])
    assert kept.payload["stack"] == reduced["stack"]
    assert "fewer lines" in (kept.detail or "")


def _result(collector: Collector, status: CollectorStatus, payload: dict) -> CollectorResult:
    return CollectorResult(collector=collector, query="q", status=status, payload=payload)


def test_a_span_is_reduced_to_what_opens_the_trace():
    payload = {
        "data": [
            {
                "attributes": {
                    "service": "plt-systeme-u-rec",
                    "resource_name": "POST /agent/register",
                    "operation_name": "pekko-http.request",
                    "trace_id": "83dad3fd464480fb47847d665ed77224",
                    "span_id": "1234",
                    "start_timestamp": "2026-08-25T05:10:00Z",
                    "custom": {"error": {"type": "java.lang.NullPointerException"}},
                }
            }
        ]
    }
    reduced = reduce_error_spans(payload, 10)
    assert reduced["count"] == 1
    assert reduced["lines"][0]["trace_id"] == "83dad3fd464480fb47847d665ed77224"
    assert reduced["lines"][0]["resource"] == "POST /agent/register"
    assert reduced["lines"][0]["error_type"] == "java.lang.NullPointerException"


# -- 3.4 the window -------------------------------------------------------------


def test_the_window_runs_back_from_the_tick_to_the_configured_lookback():
    window = collection_window(NOW, 60)
    assert window.end == NOW
    assert window.start == NOW - timedelta(minutes=60)


def test_the_window_ignores_how_long_ago_the_defect_was_first_seen():
    """A group first seen in March asks for an hour, not five months."""
    assert collection_window(NOW, 60).start > a_group().first_seen


# -- 3.2 / 3.5 what an empty collector means ------------------------------------


async def test_a_service_nobody_collects_is_not_instrumented():
    collection = await collect_group(FakeDatadogClient(), a_group(), WINDOW, CAPS)
    statuses = {result.collector: result.status for result in collection.results}
    assert statuses[Collector.ERROR_SPANS] is CollectorStatus.NOT_INSTRUMENTED
    assert statuses[Collector.ERROR_LOGS] is CollectorStatus.NOT_INSTRUMENTED
    detail = collection.results[0].detail or ""
    assert "nothing at all for these services either" in detail


async def test_evidence_datadog_counted_and_discarded_is_sampled_away_not_empty():
    """The measured case: 211,179 spans for a service whose error spans return nothing.

    Both absences are an empty list and they are opposite instructions — one says
    look elsewhere, the other names a retention filter somebody can turn on.
    """
    # Markers are matched in order and the control query is a substring of the
    # other two, so the error-predicate shapes are declared first and answer empty.
    empty_aggregate: dict = {"data": []}
    client = FakeDatadogClient(
        responses={
            "spans": {
                "@error.type": empty_aggregate,
                "status:error": empty_aggregate,
                "service:(plt-systeme-u OR plt-systeme-u-rec)": {
                    "data": [
                        {
                            "attributes": {
                                "by": {"service": "plt-systeme-u-rec"},
                                "computes": {"c0": 211179},
                            }
                        }
                    ]
                },
            }
        }
    )
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    spans = next(
        result for result in collection.results if result.collector is Collector.ERROR_SPANS
    )
    assert spans.status is CollectorStatus.SAMPLED_AWAY
    assert collection.under_matched
    assert "12,213 occurrences" in (spans.detail or "")
    assert "211,179 spans" in (spans.detail or "")
    assert "discarded" in (spans.detail or "")


async def test_the_broad_query_answers_when_the_exception_type_matches_nothing():
    """Measured: `@error.type:"…"` finds nothing over seven days, `status:error` finds 48."""
    client = FakeDatadogClient(
        responses={"spans_search": {"status:error": {"data": [{"attributes": {"service": "x"}}]}}}
    )
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    spans = next(
        result for result in collection.results if result.collector is Collector.ERROR_SPANS
    )
    assert spans.status is CollectorStatus.OK
    assert spans.query.endswith("status:error")
    assert "matched nothing" in (spans.detail or "")
    assert "may include errors that are not this defect" in (spans.detail or "")


async def test_a_collector_datadog_refuses_is_a_stated_failure_and_the_run_continues():
    client = FakeDatadogClient(
        fail={"logs": "POST /api/v2/logs/events/search returned 429: too many requests"},
        responses={"spans_search": {"@error.type": {"data": [{"attributes": {"service": "x"}}]}}},
    )
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    logs = next(result for result in collection.results if result.collector is Collector.ERROR_LOGS)
    assert logs.status is CollectorStatus.FAILED
    assert "429" in (logs.detail or "")
    # And the other two still ran.
    assert len(collection.results) == 3
    assert any(result.status is CollectorStatus.OK for result in collection.results)


async def test_a_control_that_cannot_be_run_leaves_the_absence_unexplained_rather_than_guessed():
    client = FakeDatadogClient(fail={"logs_aggregate": "500 internal error"})
    collection = await collect_group(client, a_group(), WINDOW, CAPS)
    logs = next(result for result in collection.results if result.collector is Collector.ERROR_LOGS)
    assert logs.status is CollectorStatus.EMPTY
    assert "the control query could not be run" in (logs.detail or "")


async def test_the_control_is_asked_once_per_track_not_once_per_collector():
    client = FakeDatadogClient()
    await collect_group(client, a_group(), WINDOW, CAPS)
    control = reconstruct(a_group()).control
    assert client.queries_for("spans").count(control) == 1
