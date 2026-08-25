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
            narrow='service:plt-systeme-u-rec @error.type:"…"',
            broad="service:plt-systeme-u-rec status:error",
            control="service:plt-systeme-u-rec",
        ),
        "claimed_occurrences": 5909,
        "results": [
            CollectorResult(
                collector=Collector.ERROR_SPANS,
                query='service:plt-systeme-u-rec @error.type:"…"',
                status=CollectorStatus.SAMPLED_AWAY,
                detail="Error Tracking counted 5,909 occurrences in this window and the "
                "same services returned 211,179 spans with the error predicate dropped",
            ),
            CollectorResult(
                collector=Collector.ERROR_LOGS,
                query='service:plt-systeme-u-rec @error.type:"…"',
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
