"""The Error Tracking envelope and the two rules over it (M8 1.2, 1.4, 1.5, 1.6).

Every number asserted here was measured on ``tests/fixtures/datadog/errors/
org_20260825_1h/`` — one real hour of the org, captured 2026-08-25 and not
re-capturable. See that directory's ``NOTES.md``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import ERROR_CAPTURE, captured_errors
from triage.errors.issues import Novelty, not_a_code_exception, novelty, parse_issues
from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorIssue, ErrorTrack

CAPTURE_START = datetime(2026, 8, 25, 4, 35, 24, tzinfo=UTC)
CAPTURE_END = datetime(2026, 8, 25, 5, 35, 24, tzinfo=UTC)
CAPTURE_WINDOW = TimeWindow(start=CAPTURE_START, end=CAPTURE_END)


def an_issue(**overrides: object) -> ErrorIssue:
    base: dict[str, object] = {
        "issue_id": "395eb060-7739-11f0-807a-da7ad0900005",
        "track": ErrorTrack.TRACE,
        "service": "plt-systeme-u-rec",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "error_message": "Error in query «load_contact_by_id» : Not found",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "first_seen": datetime(2025, 8, 12, tzinfo=UTC),
        "last_seen": CAPTURE_END,
        "state": "ACKNOWLEDGED",
        "occurrences": 5869,
    }
    base.update(overrides)
    return ErrorIssue.model_validate(base)


class TestParsingTheEnvelope:
    """1.2 — one call per track brings back counts and attributes together."""

    def test_joins_each_issue_to_the_count_the_search_ranked_it_by(self) -> None:
        issues = parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)

        assert len(issues) == 15
        assert [issue.occurrences for issue in issues[:3]] == [6344, 5869, 4009]
        assert all(issue.track is ErrorTrack.TRACE for issue in issues)

    def test_every_captured_issue_names_a_file_and_a_function(self) -> None:
        issues = parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)

        assert all(issue.file_path and issue.function_name for issue in issues)

    def test_reads_the_regression_block_when_the_issue_carries_one(self) -> None:
        issues = parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)
        regressed = [issue for issue in issues if issue.regressed_at is not None]

        assert len(regressed) == 3
        assert all(issue.resolved_at is not None for issue in regressed)

    def test_an_empty_track_parses_to_nothing(self) -> None:
        """The org's `logs` track answered `{"data": []}` at every window tried."""
        assert parse_issues(captured_errors("search_logs"), ErrorTrack.LOGS) == []

    def test_an_answer_without_included_yields_nothing_rather_than_ids(self) -> None:
        """Without ``include=issue`` there are counts and no attributes to decide on."""
        payload = dict(captured_errors("search_trace"))
        payload.pop("included")

        assert parse_issues(payload, ErrorTrack.TRACE) == []

    def test_an_empty_version_string_is_no_version(self) -> None:
        """All fifteen carry ``first_seen_version: ""`` — an absence, not a version."""
        issues = parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)

        assert all(issue.first_seen_version is None for issue in issues)


class TestTheCodeExceptionRule:
    """1.4 — a type and a source location, or a stated reason for skipping."""

    def test_a_type_and_a_source_location_is_a_code_exception(self) -> None:
        assert not_a_code_exception(an_issue()) is None

    def test_every_captured_issue_is_a_code_exception(self) -> None:
        issues = parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)

        assert [issue.issue_id for issue in issues if not_a_code_exception(issue)] == []

    def test_naming_neither_is_skipped_and_says_so(self) -> None:
        reason = not_a_code_exception(an_issue(error_type=None, file_path=None))

        assert reason is not None
        assert "exception type" in reason
        assert "source location" in reason

    def test_naming_only_a_type_says_which_half_is_missing(self) -> None:
        reason = not_a_code_exception(an_issue(file_path=None))

        assert reason is not None
        assert "source location" in reason
        assert "exception type" not in reason

    def test_a_function_without_a_file_is_not_a_location(self) -> None:
        """``paths`` needs a file. A synthetic lambda name alone opens nothing."""
        assert not_a_code_exception(an_issue(file_path=None, function_name="load")) is not None


class TestTheNoveltyRule:
    """1.5 and 1.6 — new, regressed, or nothing at all."""

    def test_first_seen_inside_the_window_is_new(self) -> None:
        issue = an_issue(first_seen=CAPTURE_START + timedelta(minutes=10))

        assert novelty(issue, CAPTURE_WINDOW) is Novelty.NEW

    def test_a_regression_reopened_inside_the_window_is_regressed(self) -> None:
        issue = an_issue(regressed_at=CAPTURE_START + timedelta(minutes=10))

        assert novelty(issue, CAPTURE_WINDOW) is Novelty.REGRESSED

    def test_new_and_regressed_are_told_apart(self) -> None:
        """A fix that did not hold is a different report from a defect nobody has seen."""
        inside = CAPTURE_START + timedelta(minutes=10)

        assert novelty(an_issue(first_seen=inside), CAPTURE_WINDOW) is Novelty.NEW
        assert novelty(an_issue(regressed_at=inside), CAPTURE_WINDOW) is Novelty.REGRESSED

    def test_first_seen_before_the_window_and_not_regressed_is_nothing(self) -> None:
        assert novelty(an_issue(), CAPTURE_WINDOW) is None

    def test_a_regression_older_than_the_window_is_nothing(self) -> None:
        issue = an_issue(regressed_at=CAPTURE_START - timedelta(days=7))

        assert novelty(issue, CAPTURE_WINDOW) is None

    def test_the_captured_hour_holds_nothing_new_at_all(self) -> None:
        """The measured common case: 15 issues occurring, none of them new here."""
        issues = parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)

        assert [issue.issue_id for issue in issues if novelty(issue, CAPTURE_WINDOW)] == []


@pytest.mark.parametrize("track", [ErrorTrack.TRACE, ErrorTrack.LOGS])
def test_the_capture_holds_a_search_for_every_track_the_config_declares(
    track: ErrorTrack,
) -> None:
    assert captured_errors(f"search_{track.value}", ERROR_CAPTURE) is not None
