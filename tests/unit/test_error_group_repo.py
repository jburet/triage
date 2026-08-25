"""What the repository keeps for a group that no single tick can see (M8 2.3-2.5).

The cumulative total the escalation reads, the thread every message replies
under, and how many times the group has been taken up. The merge rule is the
whole of it: a group as the grouping rule derived it is one tick's observation
and is added to what is stored; a group carrying a total is one that was read
back and changed, and is written as it stands.
"""

from datetime import UTC, datetime, timedelta

import pytest

from triage.db.repo import InMemoryRepository, merged_error_group
from triage.schemas.errors import ErrorGroup, ErrorGroupStatus, ErrorTrack, Novelty

NOW = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)
KEY = "java.lang.NullPointerException|Property.scala|get|platform"


def a_tick(occurrences: int = 4, **overrides: object) -> ErrorGroup:
    """A group exactly as ``group_issues`` produces one: no cumulative, no lifecycle."""
    base: dict[str, object] = {
        "key": KEY,
        "error_type": "java.lang.NullPointerException",
        "file_path": "zeenea.repository.orientdb.mapping.Property.scala",
        "function_name": "get",
        "repository": "platform",
        "repo_url": "github.com/zeenea/datacatalog",
        "team": "platform",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"plt-merck": occurrences},
        "occurrences": occurrences,
        "first_seen": NOW,
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


class TestTheFirstSighting:
    async def test_a_new_group_starts_its_own_cumulative_count(
        self, repo: InMemoryRepository
    ) -> None:
        stored = await repo.upsert_error_group(a_tick(4))

        assert stored.cumulative_occurrences == 4
        assert stored.cumulative_services == {"plt-merck": 4}
        assert stored.status is ErrorGroupStatus.OPEN

    async def test_it_can_be_found_by_the_key_the_rule_recomputes(
        self, repo: InMemoryRepository
    ) -> None:
        await repo.upsert_error_group(a_tick())

        assert await repo.error_group(KEY) is not None
        assert await repo.error_group("something else") is None


class TestAccumulating:
    async def test_a_second_tick_adds_to_the_total(self, repo: InMemoryRepository) -> None:
        await repo.upsert_error_group(a_tick(4))

        stored = await repo.upsert_error_group(a_tick(6))

        assert stored.occurrences == 6
        assert stored.cumulative_occurrences == 10

    async def test_the_per_service_counts_accumulate_too(self, repo: InMemoryRepository) -> None:
        """A defect that is 99% one tenant has to keep looking different from an even spread."""
        await repo.upsert_error_group(a_tick(services={"plt-merck": 100}, occurrences=100))

        stored = await repo.upsert_error_group(
            a_tick(services={"plt-merck": 100, "plt-gema": 1}, occurrences=101)
        )

        assert stored.cumulative_services == {"plt-merck": 200, "plt-gema": 1}

    async def test_the_group_keeps_the_earliest_first_seen(self, repo: InMemoryRepository) -> None:
        await repo.upsert_error_group(a_tick(first_seen=NOW - timedelta(days=3)))

        stored = await repo.upsert_error_group(a_tick(first_seen=NOW))

        assert stored.first_seen == NOW - timedelta(days=3)


class TestTheLifecycleIsNotOverwritten:
    async def test_a_tick_does_not_knock_a_reported_group_back_to_open(
        self, repo: InMemoryRepository
    ) -> None:
        reported = (await repo.upsert_error_group(a_tick())).model_copy(
            update={
                "status": ErrorGroupStatus.REPORTED,
                "analysis_count": 2,
                "analysed_at_cumulative": 400,
                "last_analysed_at": NOW,
                "thread_ts": "1756100000.000100",
                "first_report_url": "https://slack/archives/C1/p1",
            }
        )
        await repo.upsert_error_group(reported)

        stored = await repo.upsert_error_group(a_tick(5))

        assert stored.status is ErrorGroupStatus.REPORTED
        assert stored.analysis_count == 2
        assert stored.analysed_at_cumulative == 400
        assert stored.thread_ts == "1756100000.000100"
        assert stored.first_report_url == "https://slack/archives/C1/p1"

    async def test_a_group_read_back_and_changed_is_written_as_it_stands(
        self, repo: InMemoryRepository
    ) -> None:
        """The discriminator: it carries a cumulative total, so it is not a new observation."""
        await repo.upsert_error_group(a_tick(4))
        stored = await repo.error_group(KEY)
        assert stored is not None

        again = await repo.upsert_error_group(stored)

        assert again.cumulative_occurrences == 4

    async def test_a_service_a_repository_now_claims_stops_being_unmapped(
        self, repo: InMemoryRepository
    ) -> None:
        await repo.upsert_error_group(a_tick(repository=None, status=ErrorGroupStatus.UNMAPPED))

        stored = await repo.upsert_error_group(a_tick())

        assert stored.status is ErrorGroupStatus.OPEN

    async def test_and_one_it_no_longer_claims_becomes_unmapped_again(
        self, repo: InMemoryRepository
    ) -> None:
        await repo.upsert_error_group(a_tick())

        stored = await repo.upsert_error_group(
            a_tick(repository=None, status=ErrorGroupStatus.UNMAPPED)
        )

        assert stored.status is ErrorGroupStatus.UNMAPPED


class TestSeenAgainWithoutBeingNew:
    """What feeds the escalation, once Datadog will never call the issue new again (ADR-0030).

    An observation the tick made of a group it did not see arrive. It may move
    the total and nothing else — above all it may not bring a group into
    existence, because every issue the org has ever raised goes on occurring and
    a tick that created a row for each of them would report the past for ever.
    """

    async def test_a_group_nothing_knows_is_not_created(self, repo: InMemoryRepository) -> None:
        again = await repo.refresh_error_group(a_tick(6000, novelty=Novelty.CONTINUING))

        assert again is None
        assert await repo.error_group(KEY) is None

    async def test_a_group_already_known_has_the_count_added(
        self, repo: InMemoryRepository
    ) -> None:
        await repo.upsert_error_group(a_tick(4))

        again = await repo.refresh_error_group(a_tick(4, novelty=Novelty.CONTINUING))

        assert again is not None
        assert again.cumulative_occurrences == 8
        assert again.novelty is Novelty.CONTINUING
        assert (await repo.error_group(KEY)).cumulative_occurrences == 8  # type: ignore[union-attr]

    async def test_and_the_lifecycle_of_a_reported_group_untouched(
        self, repo: InMemoryRepository
    ) -> None:
        first = await repo.upsert_error_group(a_tick(4))
        await repo.upsert_error_group(
            first.model_copy(
                update={
                    "status": ErrorGroupStatus.REPORTED,
                    "analysis_count": 1,
                    "analysed_at_cumulative": 4,
                    "last_analysed_at": NOW,
                    "thread_ts": "1756100000.000100",
                }
            )
        )

        again = await repo.refresh_error_group(a_tick(50, novelty=Novelty.CONTINUING))

        assert again is not None
        assert again.status is ErrorGroupStatus.REPORTED
        assert again.analysis_count == 1
        assert again.analysed_at_cumulative == 4
        assert again.thread_ts == "1756100000.000100"
        assert again.cumulative_occurrences == 54


class TestWhatIsStillOpen:
    async def test_open_and_analysing_are_open_settled_states_are_not(
        self, repo: InMemoryRepository
    ) -> None:
        """A run that dies mid-analysis has to be recoverable, as a signal is (4.6)."""
        for index, status in enumerate(ErrorGroupStatus):
            await repo.upsert_error_group(
                a_tick(key=f"{KEY}#{index}", status=status, cumulative_occurrences=1)
            )

        open_groups = await repo.error_groups_open()

        assert {group.status for group in open_groups} == {
            ErrorGroupStatus.OPEN,
            ErrorGroupStatus.ANALYSING,
        }

    async def test_the_loudest_come_first(self, repo: InMemoryRepository) -> None:
        await repo.upsert_error_group(a_tick(4, key=f"{KEY}#quiet"))
        await repo.upsert_error_group(a_tick(400, key=f"{KEY}#loud"))

        assert [group.cumulative_occurrences for group in await repo.error_groups_open()] == [
            400,
            4,
        ]


def test_the_merge_rule_needs_no_repository_at_all() -> None:
    """It is a pure function, so both implementations can share it verbatim."""
    stored = merged_error_group(None, a_tick(4))

    assert merged_error_group(stored, a_tick(6)).cumulative_occurrences == 10


class TestServicesRaisingSince:
    """Which tenants raised a code exception, for the mapping pass to derive (M8).

    The mapping's other source is ``services_seen_since``, which reads the
    signals table — services that *alerted*. F2's tenants never alert, so
    without this the pass covering "everything whose mapping Triage has needed"
    covered none of them: on 2026-08-25 the default run returned zero services
    while seventy tenants were raising exceptions, and every report then said no
    deployed commit was known.
    """

    async def test_names_every_service_a_group_was_seen_in(self) -> None:
        repo = InMemoryRepository()
        await repo.upsert_error_group(
            a_tick(services={"plt-bred": 90, "plt-merck-qa": 10}, occurrences=100)
        )

        assert await repo.services_raising_since(NOW - timedelta(hours=1)) == [
            "plt-bred",
            "plt-merck-qa",
        ]

    async def test_ignores_a_group_last_seen_before_the_window(self) -> None:
        repo = InMemoryRepository()
        stale = NOW - timedelta(days=30)
        await repo.upsert_error_group(a_tick(first_seen=stale, last_seen=stale))

        assert await repo.services_raising_since(NOW - timedelta(hours=1)) == []

    async def test_a_service_in_two_groups_is_named_once(self) -> None:
        repo = InMemoryRepository()
        await repo.upsert_error_group(a_tick(services={"plt-bred": 4}))
        await repo.upsert_error_group(
            a_tick(key=KEY + "|other", services={"plt-bred": 7}, occurrences=7)
        )

        assert await repo.services_raising_since(NOW - timedelta(hours=1)) == ["plt-bred"]
