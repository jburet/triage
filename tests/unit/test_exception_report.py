"""What one recurring code exception looks like in Slack (M8 4.4).

The nine sections of ``docs/ticket-spec.md`` plus an exception header, and — this
being the shape a real F2 report has in this org today (ADR-0027) — it has to
read well with no evidence at all behind it.
"""

from datetime import UTC, datetime, timedelta

from tests.conftest import a_workload, load_diagnosis
from triage.report import EXCEPTION_HEADING, render_code_exception
from triage.schemas.collection import Collector, CollectorResult, CollectorStatus
from triage.schemas.common import Confidence, TimeWindow, Unknown
from triage.schemas.errors import (
    CommitChoice,
    ErrorCollection,
    ErrorGroup,
    ErrorTrack,
    ExceptionExemplar,
    Novelty,
    Reconstruction,
)
from triage.schemas.ticket import TicketSection

NOW = datetime(2026, 8, 25, 5, 35, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW - timedelta(hours=1), end=NOW)


def a_group(**overrides: object) -> ErrorGroup:
    base: dict[str, object] = {
        "key": "EntityNotFoundException|OdbClient.scala|$anonfun$load$6|platform",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "repository": "platform",
        "repo_url": "github.com/zeenea/platform",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"plt-systeme-u-rec": 5869, "plt-merck-qa": 40},
        "occurrences": 5909,
        "issue_ids": ["1e4f8c2a"],
        "sample_message": "Entity not found: load_contact_by_id",
        "counted_over": WINDOW,
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


def a_collection(**overrides: object) -> ErrorCollection:
    """The measured shape: everything counted, nothing retained."""
    base: dict[str, object] = {
        "group_key": "k",
        "window": WINDOW,
        "reconstruction": Reconstruction(
            query="service:plt-systeme-u-rec status:error",
            match='exception.type:"…" inside each span\'s custom.events',
            control="service:plt-systeme-u-rec",
        ),
        "claimed_occurrences": 5909,
        "results": [
            CollectorResult(
                collector=Collector.ERROR_SPANS,
                query="service:plt-systeme-u-rec status:error",
                status=CollectorStatus.SAMPLED_AWAY,
                detail="Error Tracking counted 5,909 occurrences in this window and the "
                "same services returned 211,179 spans with the error predicate dropped",
            ),
            CollectorResult(
                collector=Collector.ERROR_LOGS,
                query="service:plt-systeme-u-rec status:error",
                status=CollectorStatus.NOT_INSTRUMENTED,
                detail="nothing at all for these services either",
            ),
        ],
    }
    base.update(overrides)
    return ErrorCollection.model_validate(base)


def a_fallback() -> CommitChoice:
    return CommitChoice(
        commit="cafe123",
        claimed=False,
        rung="nothing claims the version this exception was first seen on",
    )


def render(group: ErrorGroup | None = None, **over: object):
    return render_code_exception(
        over.pop("diagnosis", None) or load_diagnosis("oom_payments"),
        group or a_group(),
        over.pop("workload", None) or a_workload(),
        over.pop("collection", None) or a_collection(),
        commit=over.pop("commit", None) or a_fallback(),
        source_caveat=over.pop("source_caveat", None),
        threshold=Confidence.MEDIUM,
    )


def body(report, heading: str) -> str:
    return next(section.body for section in report.sections if section.heading == heading)


def test_it_carries_the_nine_sections_and_an_exception_header():
    report = render()

    headings = [section.heading for section in report.sections]
    assert headings[0] == EXCEPTION_HEADING
    assert headings[1:] == [section.heading for section in TicketSection]


def test_the_header_names_the_type_message_count_tenants_and_the_issue():
    header = body(render(), EXCEPTION_HEADING)

    assert "zeenea.commons.exceptions.EntityNotFoundException" in header
    assert "Entity not found: load_contact_by_id" in header
    assert "5,909 between 2026-08-25 04:35 and 05:35 UTC" in header
    assert "`plt-systeme-u-rec` 5,869 · `plt-merck-qa` 40" in header
    assert "2 services of the same repository" in header
    assert "https://app.datadoghq.eu/apm/error-tracking/issue/1e4f8c2a" in header


def test_the_normal_case_says_no_version_was_recorded_rather_than_showing_a_blank():
    header = body(render(), EXCEPTION_HEADING)

    assert "recorded no application version" in header


def test_both_versions_are_named_when_the_issue_recorded_them():
    header = body(
        render(a_group(first_seen_version="501", last_seen_version="514")), EXCEPTION_HEADING
    )

    assert "first seen on `501`, last seen on `514`" in header


def test_the_jvm_symbol_is_translated_to_the_method_a_developer_would_search_for():
    header = body(render(), EXCEPTION_HEADING)

    assert "in `$anonfun$load$6` — the method `load`" in header


def test_a_derived_path_says_it_was_derived():
    header = body(render(source_caveat="`x` is a fully-qualified class name"), EXCEPTION_HEADING)

    assert "fully-qualified class name" in header


def test_a_commit_from_a_claimed_version_and_a_fallback_do_not_read_alike():
    claimed = CommitChoice(
        commit="deadbee",
        version="501",
        claimed=True,
        rung="the exception was first seen on version `501`, and the tag `501` points here",
    )

    assert "first seen on version `501`" in body(render(commit=claimed), "Location")
    assert "nothing claims the version" in body(render(), "Location")


def test_the_discarded_evidence_is_stated_beside_the_evidence_that_exists():
    """ADR-0027: the absence is the finding, and a report that only said 'empty' gets
    nobody to turn a retention filter on."""
    evidence = body(render(), "Evidence")

    assert "What was searched for and not found:" in evidence
    assert "[error_spans] sampled_away" in evidence
    assert "211,179 spans with the error predicate dropped" in evidence
    assert "[error_logs] not_instrumented" in evidence


def test_a_second_report_says_which_one_it_is_and_points_at_the_first():
    header = body(
        render(a_group(analysis_count=4, cumulative_occurrences=42_000)), EXCEPTION_HEADING
    )

    assert "*Report 4* for this group" in header
    assert "42,000 occurrences" in header
    assert "top of this thread" in header


def test_a_low_confidence_report_leads_with_the_exception_and_not_a_cause():
    report = render(diagnosis=load_diagnosis("latency_low_confidence"))

    assert not report.leads_with_cause
    assert "`EntityNotFoundException` raised 5,909 times in 2 tenants" in report.headline
    assert report.service == "platform"


def test_a_confident_report_leads_with_the_cause():
    report = render()

    assert report.leads_with_cause
    assert report.headline.startswith(":dart: *platform*")


def test_the_repository_is_stated_even_when_no_analysis_selected_a_location():
    """Measured on the live run of 2026-08-25: every analysis failed, and the report
    said Unknown about the one thing the grouping rule was certain of."""
    base = load_diagnosis("latency_low_confidence")
    diagnosis = base.model_copy(
        update={
            "location": base.location.model_copy(
                update={"repo": Unknown(reason="no analysed hypothesis was selected")}
            )
        }
    )

    location = body(render(diagnosis=diagnosis), "Location")

    assert "github.com/zeenea/platform" in location
    assert "all run that repository" in location


class TestOccurrenceWindow:
    """The count is over the span the issue was seen, not the collection's hour.

    Measured on 2026-08-25: a tick replaying six hours collected over the last
    one, and the header read "126 occurrences between 06:24 and 07:24" for a
    burst that ran 02:29 to 03:12. The count and the window came from different
    clocks, which is the one thing a report may never do.
    """

    def test_dates_the_count_by_the_window_it_was_counted_over(self) -> None:
        polled = TimeWindow(start=NOW - timedelta(hours=6), end=NOW)
        report = render(a_group(counted_over=polled, occurrences=126))
        header = next(s for s in report.sections if s.heading == EXCEPTION_HEADING)
        dated = f"126 between {polled.start:%Y-%m-%d %H:%M} and {polled.end:%Y-%m-%d %H:%M} UTC"
        assert dated in header.body

    def test_never_dates_it_by_the_collection_hour(self) -> None:
        """The collection looks for evidence in the last hour; the count is not from there."""
        polled = TimeWindow(start=NOW - timedelta(hours=6), end=NOW)
        assert polled.start != WINDOW.start
        report = render(a_group(counted_over=polled, occurrences=126))
        header = next(s for s in report.sections if s.heading == EXCEPTION_HEADING)
        assert f"between {WINDOW.start:%Y-%m-%d %H:%M}" not in header.body

    def test_never_dates_it_by_the_issue_lifetime(self) -> None:
        """first_seen is when the defect appeared, not when these occurrences happened."""
        polled = TimeWindow(start=NOW - timedelta(hours=6), end=NOW)
        group = a_group(counted_over=polled, occurrences=126)
        report = render(group)
        header = next(s for s in report.sections if s.heading == EXCEPTION_HEADING)
        assert f"between {group.first_seen:%Y-%m-%d %H:%M}" not in header.body

    def test_names_the_day_at_both_ends_when_the_window_crosses_midnight(self) -> None:
        """A 24-hour backfill printed "between 2026-08-24 07:33 and 07:33 UTC"."""
        polled = TimeWindow(start=NOW - timedelta(hours=24), end=NOW)

        header = body(render(a_group(counted_over=polled, occurrences=126)), EXCEPTION_HEADING)

        assert f"{polled.start:%Y-%m-%d %H:%M}" in header
        assert f"{polled.end:%Y-%m-%d %H:%M}" in header

    def test_names_the_day_once_when_the_window_is_inside_it(self) -> None:
        polled = TimeWindow(start=NOW - timedelta(hours=2), end=NOW)

        header = body(render(a_group(counted_over=polled, occurrences=126)), EXCEPTION_HEADING)

        assert f"between {polled.start:%Y-%m-%d %H:%M} and {polled.end:%H:%M} UTC" in header

    def test_says_so_when_the_group_carries_no_window(self) -> None:
        report = render(a_group(counted_over=None, occurrences=126))
        header = next(s for s in report.sections if s.heading == EXCEPTION_HEADING)
        assert "126 in this tick" in header.body


class TestManyTenants:
    """A group that spans the estate names its worst, and counts the rest.

    Measured on 2026-08-25: one PSQLException grouped 66 tenants and 37,861
    occurrences. Naming all 66 inline is a wall nobody reads; naming ten and
    dropping 56 silently is the failure ADR-0026's per-service counts exist to
    prevent. So the tail is summed and said out loud.
    """

    def _spread(self, tenants: int) -> ErrorGroup:
        services = {f"plt-t{index:02d}": tenants - index for index in range(tenants)}
        return a_group(services=services, occurrences=sum(services.values()))

    def test_names_the_worst_tenants_in_order(self) -> None:
        header = body(render(self._spread(66)), EXCEPTION_HEADING)

        assert "`plt-t00` 66" in header
        assert header.index("`plt-t00`") < header.index("`plt-t01`")

    def test_counts_the_tail_rather_than_dropping_it(self) -> None:
        group = self._spread(66)

        header = body(render(group), EXCEPTION_HEADING)

        assert "56 more" in header
        named = sorted(group.services.values(), reverse=True)[:10]
        assert f"{sum(group.services.values()) - sum(named):,}" in header

    def test_a_small_group_names_every_tenant_and_summarises_nothing(self) -> None:
        header = body(render(self._spread(4)), EXCEPTION_HEADING)

        assert "`plt-t03` 1" in header
        assert "more" not in header


class TestARetainedOccurrence:
    """ADR-0029 — what an F2 report carries once a real stack has been retrieved."""

    STACK = (
        "zeenea.service.api.ScannerUpsertItemException: TooBusyIndexingException on item upsert\n"
        "\tat zeenea.service.api.ScannerService$$anonfun$upsertItem$9."
        "$anonfun$applyOrElse$3(ScannerService.scala:124)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:1)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:2)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:3)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:4)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:5)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:6)\n"
        "\tat io.opentelemetry.javaagent.a.run(A.java:7)\n"
        "Caused by: zeenea.commons.exceptions.TooBusyIndexingException: 1025 index events\n"
        "\tat zeenea.datacatalog.loadcontrol.LoadControl.isOverloaded(LoadControl.scala:14)\n"
    )

    def collection(self):
        return a_collection(
            exemplar=ExceptionExemplar(
                error_type="zeenea.service.api.ScannerUpsertItemException",
                message="TooBusyIndexingException on item upsert",
                stack=self.STACK,
                frames=[
                    "zeenea/service/api/ScannerService.scala:124",
                    "zeenea/datacatalog/loadcontrol/LoadControl.scala:14",
                ],
                trace_id="3a83b9a36ce25524334abbbb39f0072a",
                service="plt-merck-qa",
                operation="grpc.server.request",
                at="2026-08-25T03:33:15.967Z",
            )
        )

    def test_the_evidence_section_shows_the_stack_and_the_trace_that_carried_it(self):
        evidence = body(render(collection=self.collection()), "Evidence")

        assert "One retained occurrence" in evidence
        assert "trace `3a83b9a36ce25524334abbbb39f0072a`" in evidence
        assert "`grpc.server.request`" in evidence
        assert "ScannerService.scala:124" in evidence

    def test_the_caused_by_chain_survives_the_slack_bound(self):
        """A head-and-tail cut would show seven agent frames and lose the cause."""
        evidence = body(render(collection=self.collection()), "Evidence")

        assert "Caused by: zeenea.commons.exceptions.TooBusyIndexingException" in evidence
        assert "LoadControl.scala:14" in evidence
        assert "… 2 more frames" in evidence

    def test_it_stops_saying_nothing_was_found_when_something_was(self):
        no_evidence = load_diagnosis("oom_payments").model_copy(update={"evidence": []})
        evidence = body(render(diagnosis=no_evidence, collection=self.collection()), "Evidence")

        assert "No checkable evidence was produced." not in evidence
        assert "what Datadog retained is below" in evidence

    def test_an_observed_frame_and_a_derived_path_do_not_read_alike(self):
        located = body(render(collection=self.collection()), "Location")

        assert "*Stack frames:* `zeenea/service/api/ScannerService.scala:124`" in located
        assert "the file and the line are observed" in located
        assert "*Stack frames:*" not in body(render(), "Location")
