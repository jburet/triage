"""One exception across tenants is one finding (M8 2.1, 2.2; ADR-0026).

Every number asserted here was measured on
``tests/fixtures/datadog/errors/org_20260825_1h/`` — one real hour of the org,
captured 2026-08-25 and not re-capturable. The collapse the ADR argues from is
in that hour: ``EntityNotFoundException`` at ``OdbClient.scala:$anonfun$load$6``
in six tenants, which without grouping is six reports of one line of code.
"""

from tests.conftest import captured_errors
from triage.errors.grouping import ServiceRepository, group_issues
from triage.errors.issues import parse_issues
from triage.schemas.errors import ErrorIssue, ErrorTrack, Novelty

PLATFORM = ServiceRepository(
    repository="platform", repo_url="github.com/zeenea/datacatalog", team="platform"
)
SCANNER = ServiceRepository(repository="scanner", repo_url="github.com/zeenea/scanner", team="core")

ODB_CLIENT = "zeenea.repository.orientdb.OdbClient.scala"
ENTITY_NOT_FOUND = "zeenea.commons.exceptions.EntityNotFoundException"


def captured_issues() -> list[ErrorIssue]:
    return parse_issues(captured_errors("search_trace"), ErrorTrack.TRACE)


def mono_tenant(service: str) -> ServiceRepository | None:
    """The mono-tenancy rule as config.yaml states it: every ``plt-*`` is one repository."""
    return PLATFORM if service.startswith("plt-") else None


def an_issue(service: str, occurrences: int = 1, **overrides: object) -> ErrorIssue:
    base: dict[str, object] = {
        "issue_id": f"{service}-1",
        "track": ErrorTrack.TRACE,
        "service": service,
        "occurrences": occurrences,
        "error_type": ENTITY_NOT_FOUND,
        "error_message": "Error in query «load_contact_by_id» : Not found",
        "file_path": ODB_CLIENT,
        "function_name": "$anonfun$load$6",
        "first_seen": "2026-08-25T04:40:00Z",
        "last_seen": "2026-08-25T05:30:00Z",
        "state": "OPEN",
    }
    base.update(overrides)
    return ErrorIssue.model_validate(base)


class TestOneDefectHoweverManyTenants:
    """2.1 — the same code in different customers' instances is one group."""

    def test_the_captured_hour_collapses_to_the_measured_groups(self) -> None:
        """15 issues, 7 (type, file, function) triples — the rule, and the measurement."""
        groups = group_issues(captured_issues(), mono_tenant)

        assert len(captured_issues()) == 15
        assert len(groups) == 7

    def test_six_tenants_raising_it_from_one_line_are_one_group(self) -> None:
        groups = group_issues(captured_issues(), mono_tenant)

        biggest = groups[0]
        assert biggest.error_type == ENTITY_NOT_FOUND
        assert biggest.file_path == ODB_CLIENT
        assert biggest.function_name == "$anonfun$load$6"
        assert biggest.repository == "platform"

    def test_and_the_group_names_every_service_and_the_count_in_each(self) -> None:
        """The mitigation for the flattening ADR-0026 admits to. Never summed away."""
        biggest = group_issues(captured_issues(), mono_tenant)[0]

        assert biggest.services == {
            "plt-systeme-u-rec": 5869,
            "plt-autostrade": 4009,
            "plt-systeme-u": 850,
            "plt-pon": 29,
            "plt-pon-uat": 4,
            "plt-merck-qa": 2,
        }
        assert biggest.occurrences == 10763

    def test_a_group_carries_the_issue_ids_it_was_built_from(self) -> None:
        biggest = group_issues(captured_issues(), mono_tenant)[0]

        assert len(biggest.issue_ids) == 6

    def test_the_key_is_stable_across_ticks(self) -> None:
        """No store is consulted to find a group — the fourth occurrence recomputes it."""
        first = group_issues(captured_issues(), mono_tenant)
        again = group_issues(list(reversed(captured_issues())), mono_tenant)

        assert {group.key for group in first} == {group.key for group in again}

    def test_groups_come_back_worst_first(self) -> None:
        groups = group_issues(captured_issues(), mono_tenant)

        assert [group.occurrences for group in groups] == sorted(
            (group.occurrences for group in groups), reverse=True
        )

    def test_a_regressed_issue_makes_its_group_a_regression(self) -> None:
        issues = [an_issue("plt-merck"), an_issue("plt-gema")]

        groups = group_issues(issues, mono_tenant, regressed={"plt-gema-1"})

        assert groups[0].novelty is Novelty.REGRESSED

    def test_a_group_nothing_regressed_in_is_new(self) -> None:
        groups = group_issues([an_issue("plt-merck")], mono_tenant)

        assert groups[0].novelty is Novelty.NEW


class TestWhatStaysApart:
    """2.2 — the repository is half the key, and an unresolved service is its own group."""

    def test_the_same_exception_in_two_repositories_is_two_groups(self) -> None:
        issues = [an_issue("plt-merck"), an_issue("scanner-eu")]

        groups = group_issues(
            issues, lambda service: PLATFORM if service.startswith("plt-") else SCANNER
        )

        assert len(groups) == 2
        assert {group.repository for group in groups} == {"platform", "scanner"}

    def test_a_service_no_repository_claims_is_its_own_group(self) -> None:
        issues = [an_issue("plt-merck", 5), an_issue("orphan-a", 7)]

        groups = group_issues(issues, mono_tenant)

        orphan = next(group for group in groups if group.repository is None)
        assert orphan.services == {"orphan-a": 7}

    def test_and_is_reported_rather_than_analysed(self) -> None:
        orphan = group_issues([an_issue("orphan-a")], mono_tenant)[0]

        assert orphan.analysable is False
        assert orphan.unanalysable_reason is not None
        assert "orphan-a" in orphan.unanalysable_reason

    def test_two_unresolved_services_are_not_merged_with_each_other(self) -> None:
        """Nothing says they run the same code; merging them would be the guess."""
        issues = [an_issue("orphan-a"), an_issue("orphan-b")]

        groups = group_issues(issues, mono_tenant)

        assert len(groups) == 2

    def test_a_different_source_location_is_a_different_defect(self) -> None:
        issues = [
            an_issue("plt-merck"),
            an_issue("plt-gema", file_path="zeenea.repository.orientdb.mapping.Query.scala"),
        ]

        groups = group_issues(issues, mono_tenant)

        assert len(groups) == 2

    def test_the_message_is_not_part_of_the_key(self) -> None:
        """Measured: keying on the message splits the six-tenant group into three.

        The captured hour's six ``OdbClient`` issues carry six different queried
        entities in one message shape, so a key that reads the message reports
        the row that was missing rather than the code that could not handle it
        missing. ADR-0026 keeps the message out and names it as the finer key to
        reach for only if a group is ever shown to have merged two defects.
        """
        issues = [
            an_issue("plt-merck", error_message="Error in query «load_contact_by_id» : Not found"),
            an_issue("plt-gema", error_message="Error in query «load_user_by_email» : Not found"),
        ]

        groups = group_issues(issues, mono_tenant)

        assert len(groups) == 1
        assert groups[0].sample_message is not None


class TestAnEmptyTick:
    """The common tick: nothing was new or regressed, so there is nothing to group."""

    def test_no_issues_is_no_groups(self) -> None:
        assert group_issues([], mono_tenant) == []
