"""Reading an exception out of an OpenTelemetry span (M8 3.6, ADR-0029).

Every payload here is `tests/fixtures/datadog/errors/otel_stacks_20260825/`, captured
live on 2026-08-25. The numbers asserted were measured on it, not chosen.
"""

import json
from pathlib import Path

from triage.errors.otel import application_frames, exceptions_in, span_exceptions

FIXTURES = Path(__file__).parent.parent / "fixtures" / "datadog" / "errors"
CAPTURE = FIXTURES / "otel_stacks_20260825"

SCANNER = "zeenea.service.api.ScannerUpsertItemException"


def spans(service: str) -> dict:
    return json.loads((CAPTURE / f"spans_{service}.json").read_text())


# -- 3.6 the exception is inside custom.events, JSON-encoded --------------------


def test_the_exception_is_read_out_of_the_json_string_datadog_calls_custom_events():
    found = exceptions_in(spans("plt-merck-qa"))
    assert len(found) == 20
    first = found[0]
    assert first.error_type == SCANNER
    assert first.message.startswith("TooBusyIndexingException on item upsert")
    assert first.service == "plt-merck-qa"
    assert first.trace_id == "3a83b9a36ce25524334abbbb39f0072a"
    assert first.span_id
    assert first.at == "2026-08-25T03:33:15.967Z"
    assert first.operation == "grpc.server.request"


def test_the_caused_by_chain_is_kept_whole():
    """The cause is the half a developer acts on; a stack cut at the first blank line
    would end at `ScannerService.scala:124` and never name `LoadControl.scala:14`."""
    stack = exceptions_in(spans("plt-merck-qa"))[0].stacktrace
    assert "Caused by: zeenea.commons.exceptions.TooBusyIndexingException" in stack
    assert "LoadControl.scala:14" in stack
    assert len(stack) == 2334


def test_a_span_with_no_events_no_exception_or_broken_json_is_skipped_and_never_raises():
    assert span_exceptions({}) == []
    assert span_exceptions({"attributes": {"custom": {}}}) == []
    assert span_exceptions({"attributes": {"custom": {"events": "{not json"}}}) == []
    assert span_exceptions({"attributes": {"custom": {"events": '{"name": "exception"}'}}}) == []
    assert span_exceptions({"attributes": {"custom": {"events": '[{"name": "message"}]'}}}) == []
    assert span_exceptions({"attributes": {"custom": {"events": "[null, 3]"}}}) == []


def test_an_error_span_that_carries_no_exception_event_at_all_contributes_nothing():
    """Measured: 20 error spans for `plt-autostrade`, only 6 with an exception event."""
    found = exceptions_in(spans("plt-autostrade"))
    assert len(found) == 6
    assert {event.error_type for event in found} == {
        "java.net.ConnectException",
        "org.postgresql.util.PSQLException",
    }


# -- 3.7 the frames, filtered to the application's own code --------------------


def test_a_frame_becomes_a_repository_relative_path_and_a_line():
    frames = application_frames(exceptions_in(spans("plt-merck-qa"))[0].stacktrace)
    assert [frame.located for frame in frames] == [
        "zeenea/service/api/ScannerService.scala:124",
        "zeenea/server/ZeeneaReferentielAppContext.scala:162",
        "zeenea/service/api/ScannerService.scala:194",
        "zeenea/service/api/ScannerService.scala:153",
        "zeenea/datacatalog/loadcontrol/LoadControl.scala:14",
        "zeenea/service/api/ScannerService.scala:149",
        "zeenea/service/api/ScannerService.scala:146",
    ]
    assert frames[0].file == "ScannerService.scala"
    assert frames[0].method == "$anonfun$applyOrElse$3"


def test_the_runtime_is_not_the_application():
    """Eleven of the eighteen frames are the agent or the standard library."""
    stack = (
        "boom\n"
        "\tat $anonfun$restartAsync$1 @ io.opentelemetry.javaagent.bootstrap.executors"
        ".ContextPropagatingRunnable.run(ContextPropagatingRunnable.java:37)\n"
        "\tat scala.Option.fold(Option.scala:263)\n"
        "\tat java.base/java.util.concurrent.ThreadPoolExecutor.runWorker"
        "(ThreadPoolExecutor.java:1136)\n"
        "\tat zeenea.a.B.c(B.scala:9)\n"
    )
    assert [frame.located for frame in application_frames(stack)] == ["zeenea/a/B.scala:9"]


def test_a_frame_whose_package_cannot_be_read_off_the_class_is_left_out_of_the_paths():
    assert application_frames("boom\n\tat Unknown(Unknown Source)") == ()
    assert application_frames("boom\n\tat com.acme.run(Other.scala:3)") == ()
    assert application_frames(None) == ()
    assert application_frames("") == ()


def test_the_same_line_twice_in_a_row_is_one_path():
    """`manageScannerStatus$8` appears at 153 twice, once bare and once as a `flatMap @`."""
    located = [
        frame.located
        for frame in application_frames(exceptions_in(spans("plt-merck"))[0].stacktrace)
    ]
    assert len(located) == len(set(located))
