"""What a tick makes of the issues it looked at (M8 2.1-2.6).

Through the whole ``error_poller`` graph, against the captured hour. Nothing in
the capture was new or regressed inside the hour it was taken in — that is the
common tick and it has its own test here — so the other tests move every issue's
``first_seen`` into the window and let the fifteen real issues, with their real
occurrence counts, be the tick's news. The seven groups they collapse into, and
the five that clear the floor of ten, are the measurement and not a fixture.

No model is asked anything on this path. Grouping is a rule (ADR-0026) and the
gate is arithmetic (ADR-0025).
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import build_deps, captured_errors, declaring, fake_error_datadog, run_config
from triage.config import Config, RepoKind
from triage.db.repo import InMemoryRepository
from triage.errors.gate import GateOutcome
from triage.errors.grouping import group_key
from triage.graphs.error_poller import graph
from triage.integrations.datadog import FakeDatadogClient
from triage.nodes.poll_errors import POLLER_NAME
from triage.schemas.errors import ErrorGroupStatus, Novelty

CAPTURE_END = datetime(2026, 8, 25, 5, 35, 24, tzinfo=UTC)
TICK = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)

ENTITY_NOT_FOUND = "zeenea.commons.exceptions.EntityNotFoundException"
ODB_CLIENT = "zeenea.repository.orientdb.OdbClient.scala"
ODATABASE = "com.orientechnologies.orient.core.exception.ODatabaseException"
ODATABASE_FILE = "com.orientechnologies.orient.core.db.document.ODatabaseDocumentEmbedded.java"

LOUDEST = group_key(ENTITY_NOT_FOUND, ODB_CLIENT, "$anonfun$load$6", "platform", "plt-systeme-u")
"""The six-tenant group: 10,763 occurrences in the reference hour."""

QUIETEST = group_key(ODATABASE, ODATABASE_FILE, "executeReadRecord", "platform", "plt-merck")
"""Two tenants, two occurrences each. Four an hour — ADR-0025's slow bleed, measured."""


def platform_config(**errors: int) -> Config:
    """The test config with the mono-tenant platform declared, as the shipped one has it."""
    config = declaring(
        "github.com/zeenea/datacatalog",
        team="platform",
        kind=RepoKind.APPLICATION,
        serves=("plt-*",),
        image_name="platform",
    )
    return config.model_copy(update={"errors": config.errors.model_copy(update=errors)})


def new_at(first_seen: datetime) -> FakeDatadogClient:
    """The captured search with every issue first seen at one fixed moment.

    The occurrence counts, services, types and source locations are untouched:
    only the moment moves, so that an hour in which nothing was new can exercise
    what a tick does with issues that are — and so that a *later* tick, whose
    window that moment has fallen out of, meets the same issues as Datadog will
    really present them: still occurring, and never new again.
    """
    payload = captured_errors("search_trace")
    stamp = int(first_seen.timestamp() * 1000)
    for issue in payload["included"]:
        issue["attributes"]["first_seen"] = stamp
    return FakeDatadogClient(
        responses={"error_issues": {"track:trace": payload, "track:logs": {"data": []}}}
    )


def news_at(now: datetime) -> FakeDatadogClient:
    """Issues that all went new ten minutes before this tick."""
    return new_at(now - timedelta(minutes=10))


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


async def tick(
    repo: InMemoryRepository,
    now: datetime,
    config: Config | None = None,
    first_seen: datetime | None = None,
) -> dict:
    """One tick whose window ends at ``now``, on a poller that ran half an hour ago.

    ``first_seen`` fixes the moment every issue went new. Left out, it moves with
    the tick and every issue is news; given once and reused across ticks, it is
    the real shape — new in one window and merely occurring in all the others.
    """
    datadog = news_at(now) if first_seen is None else new_at(first_seen)
    deps = build_deps(config or platform_config(), datadog=datadog, repo=repo)
    await repo.set_watermark(POLLER_NAME, now - timedelta(minutes=30))
    return await graph.ainvoke({"now": now}, config=run_config(deps))


class TestTheEmptyTick:
    """The common case, and the one the capture actually replays."""

    async def test_a_tick_where_nothing_was_new_groups_nothing(
        self, repo: InMemoryRepository
    ) -> None:
        deps = build_deps(platform_config(), datadog=fake_error_datadog(), repo=repo)

        state = await graph.ainvoke({"now": CAPTURE_END}, config=run_config(deps))

        assert state["unchanged"] == 15
        assert state["groups"] == []
        assert state["held_back"] == 0
        assert state["deferred"] == []
        assert await repo.error_groups_open() == []


class TestOneExceptionHoweverManyTenants:
    """2.1 through the graph: the repository comes from config.yaml's serves pattern."""

    async def test_the_fifteen_issues_become_seven_groups(self, repo: InMemoryRepository) -> None:
        state = await tick(repo, TICK)

        assert len(state["groups"]) == 7

    async def test_the_six_tenants_of_one_defect_are_one_group(
        self, repo: InMemoryRepository
    ) -> None:
        state = await tick(repo, TICK)

        loudest = next(group for group in state["groups"] if group.key == LOUDEST)
        assert loudest.repository == "platform"
        assert loudest.repo_url == "github.com/zeenea/datacatalog"
        assert loudest.team == "platform"
        assert loudest.services == {
            "plt-systeme-u-rec": 5869,
            "plt-autostrade": 4009,
            "plt-systeme-u": 850,
            "plt-pon": 29,
            "plt-pon-uat": 4,
            "plt-merck-qa": 2,
        }

    async def test_the_per_service_counts_survive_into_the_stored_row(
        self, repo: InMemoryRepository
    ) -> None:
        """The mitigation ADR-0026 offers for flattening a tenant-specific defect."""
        await tick(repo, TICK)

        stored = await repo.error_group(LOUDEST)
        assert stored is not None
        assert stored.cumulative_services["plt-systeme-u-rec"] == 5869
        assert sum(stored.cumulative_services.values()) == stored.cumulative_occurrences


class TestNoTreeToRead:
    """2.2 — a service no repository claims is its own group, reported, never analysed."""

    async def test_every_group_is_unmapped_when_nothing_declares_the_platform(
        self, repo: InMemoryRepository, config: Config
    ) -> None:
        state = await tick(repo, TICK, config=config)

        assert len(state["unmapped"]) == len(state["groups"])
        assert state["analysing"] == []

    async def test_and_each_tenant_is_its_own_group(
        self, repo: InMemoryRepository, config: Config
    ) -> None:
        """Twelve services, fifteen issues: nothing joins them, so nothing is merged."""
        state = await tick(repo, TICK, config=config)

        assert len(state["groups"]) == 15
        assert all(len(group.services) == 1 for group in state["groups"])

    async def test_the_row_says_why_it_will_never_be_analysed(
        self, repo: InMemoryRepository, config: Config
    ) -> None:
        state = await tick(repo, TICK, config=config)

        group = state["groups"][0]
        assert group.status is ErrorGroupStatus.UNMAPPED
        assert group.unanalysable_reason is not None
        assert next(iter(group.services)) in group.unanalysable_reason


class TestThePersistenceGate:
    """2.3 — below the floor is persisted with its count and analysed nothing."""

    async def test_the_quiet_groups_are_held_back(self, repo: InMemoryRepository) -> None:
        state = await tick(repo, TICK)

        assert state["held_back"] == 2
        assert QUIETEST in [
            decision.group.key
            for decision in state["decisions"]
            if decision.outcome is GateOutcome.HELD_BACK
        ]

    async def test_a_held_back_group_is_stored_with_its_count(
        self, repo: InMemoryRepository
    ) -> None:
        await tick(repo, TICK)

        stored = await repo.error_group(QUIETEST)
        assert stored is not None
        assert stored.occurrences == 4
        assert stored.cumulative_occurrences == 4
        assert stored.status is ErrorGroupStatus.OPEN
        assert stored.analysis_count == 0

    async def test_and_stays_open_for_a_later_tick_to_find(self, repo: InMemoryRepository) -> None:
        await tick(repo, TICK)

        assert QUIETEST in [group.key for group in await repo.error_groups_open()]


class TestTheSlowBleed:
    """2.4 — four an hour, every hour, never ten in one tick.

    Datadog marks an issue new exactly *once*, so every tick after the first
    meets this defect as an issue that is neither new nor regressed. The
    escalation is fed by those (ADR-0030) or it is fed by nothing: before that,
    a group held back below the floor was never observed again and its
    cumulative total never moved, so this behaviour could not fire at all.
    """

    FIRST_SEEN = TICK - timedelta(minutes=10)

    async def test_it_is_analysed_once_the_cumulative_count_crosses(
        self, repo: InMemoryRepository
    ) -> None:
        taken_at = None
        for hour in range(30):
            state = await tick(repo, TICK + timedelta(hours=hour), first_seen=self.FIRST_SEEN)
            if hour:
                assert state["new"] == [], "Datadog calls an issue new in one window only"
            if QUIETEST in [group.key for group in state["analysing"]]:
                taken_at = hour
                break

        assert taken_at == 24, "four an hour crosses a hundred on the twenty-fifth tick"

    async def test_and_was_below_the_floor_on_every_one_of_those_ticks(
        self, repo: InMemoryRepository
    ) -> None:
        for hour in range(25):
            state = await tick(repo, TICK + timedelta(hours=hour), first_seen=self.FIRST_SEEN)
            quiet = next(group for group in state["groups"] if group.key == QUIETEST)
            assert quiet.occurrences == 4

        stored = await repo.error_group(QUIETEST)
        assert stored is not None
        assert stored.cumulative_occurrences == 100
        assert stored.analysis_count == 1
        assert stored.analysed_at_cumulative == 100

    async def test_the_later_ticks_saw_it_again_rather_than_saw_it_arrive(
        self, repo: InMemoryRepository
    ) -> None:
        await tick(repo, TICK, first_seen=self.FIRST_SEEN)

        state = await tick(repo, TICK + timedelta(hours=1), first_seen=self.FIRST_SEEN)

        assert state["seen_again"] == 7, "every group of the hour, none of them news"
        assert state["new"] == []
        assert state["analysing"] == [], "seeing a group again is not reporting it"
        quiet = next(group for group in state["groups"] if group.key == QUIETEST)
        assert quiet.novelty is Novelty.CONTINUING
        assert quiet.cumulative_occurrences == 8
        assert state["held_back"] == 2

    async def test_a_group_no_tick_ever_saw_arrive_is_not_created_by_one(
        self, repo: InMemoryRepository
    ) -> None:
        """The org's whole backlog goes on occurring; a tick that rowed it would report it."""
        state = await tick(repo, TICK, first_seen=TICK - timedelta(days=9))

        assert state["unchanged"] == 15
        assert state["groups"] == []
        assert await repo.error_groups_open() == []


class TestNotReportedTwice:
    """2.5 — already analysed stays quiet, and the next report knows it is the next."""

    async def test_the_next_tick_does_not_take_the_same_group_up_again(
        self, repo: InMemoryRepository
    ) -> None:
        first = await tick(repo, TICK)
        assert LOUDEST in [group.key for group in first["analysing"]]

        second = await tick(repo, TICK + timedelta(hours=1))

        assert LOUDEST not in [group.key for group in second["analysing"]]
        assert [
            decision.outcome for decision in second["decisions"] if decision.group.key == LOUDEST
        ] == [GateOutcome.SETTLED]

    async def test_the_group_records_which_occurrence_the_next_report_is(
        self, repo: InMemoryRepository
    ) -> None:
        await tick(repo, TICK)

        stored = await repo.error_group(LOUDEST)
        assert stored is not None
        assert stored.analysis_count == 1
        assert stored.analysed_at_cumulative == stored.cumulative_occurrences
        assert stored.status is ErrorGroupStatus.ANALYSING

    async def test_the_thread_the_first_report_opened_survives_every_later_tick(
        self, repo: InMemoryRepository
    ) -> None:
        """4.5 needs the row to hold the thread; 2.5 needs the next report to link the first."""
        await tick(repo, TICK)
        first = await repo.error_group(LOUDEST)
        assert first is not None
        await repo.upsert_error_group(
            first.model_copy(
                update={
                    "status": ErrorGroupStatus.REPORTED,
                    "thread_ts": "1756100000.000100",
                    "first_report_url": "https://slack/archives/C1/p1756100000000100",
                }
            )
        )

        await tick(repo, TICK + timedelta(hours=1))

        stored = await repo.error_group(LOUDEST)
        assert stored is not None
        assert stored.thread_ts == "1756100000.000100"
        assert stored.first_report_url == "https://slack/archives/C1/p1756100000000100"
        assert stored.analysis_count == 1
        assert stored.status is ErrorGroupStatus.REPORTED

    async def test_it_is_taken_up_again_once_the_cooldown_has_passed(
        self, repo: InMemoryRepository
    ) -> None:
        await tick(repo, TICK)
        config = platform_config()

        state = await tick(repo, TICK + timedelta(hours=config.errors.reanalyse_after))

        assert LOUDEST in [group.key for group in state["analysing"]]
        stored = await repo.error_group(LOUDEST)
        assert stored is not None
        assert stored.analysis_count == 2

    async def test_a_regression_reopens_it_without_waiting(self, repo: InMemoryRepository) -> None:
        """The one issue in the capture that regressed, on the window it regressed in."""
        await tick(repo, TICK)
        regressed = group_key(
            ENTITY_NOT_FOUND,
            "zeenea.repository.orientdb.mapping.Query.scala",
            "load",
            "platform",
            "plt-systeme-u",
        )
        stored = await repo.error_group(regressed)
        assert stored is not None
        assert stored.analysis_count == 1

        deps = build_deps(platform_config(), datadog=fake_error_datadog(), repo=repo)
        await repo.set_watermark(POLLER_NAME, datetime(2026, 8, 18, 16, tzinfo=UTC))
        state = await graph.ainvoke(
            {"now": datetime(2026, 8, 18, 17, tzinfo=UTC)}, config=run_config(deps)
        )

        assert regressed in [group.key for group in state["analysing"]]
        again = await repo.error_group(regressed)
        assert again is not None
        assert again.analysis_count == 2


class TestThePerTickCap:
    """2.6 — at most `max_groups_per_tick`, loudest first, the rest named."""

    async def test_a_tick_takes_up_no_more_than_the_cap(self, repo: InMemoryRepository) -> None:
        state = await tick(repo, TICK, config=platform_config(max_groups_per_tick=3))

        assert len(state["analysing"]) == 3

    async def test_the_loudest_go_first(self, repo: InMemoryRepository) -> None:
        state = await tick(repo, TICK, config=platform_config(max_groups_per_tick=3))

        assert [group.occurrences for group in state["analysing"]] == [10763, 7829, 435]

    async def test_the_overflow_is_named_rather_than_dropped(
        self, repo: InMemoryRepository
    ) -> None:
        state = await tick(repo, TICK, config=platform_config(max_groups_per_tick=3))

        by_key = {group.key: group for group in state["groups"]}
        assert [by_key[key].occurrences for key in state["deferred"]] == [200, 15]

    async def test_a_deferred_group_is_persisted_and_taken_up_by_a_later_tick(
        self, repo: InMemoryRepository
    ) -> None:
        config = platform_config(max_groups_per_tick=3)
        first = await tick(repo, TICK, config=config)
        deferred = set(first["deferred"])
        assert deferred

        second = await tick(repo, TICK + timedelta(hours=1), config=config)

        assert deferred & {group.key for group in second["analysing"]}

    async def test_a_group_held_back_does_not_spend_the_cap(self, repo: InMemoryRepository) -> None:
        """Two groups are below the floor; they must not crowd out a loud one."""
        state = await tick(repo, TICK)

        assert len(state["analysing"]) == 5
        assert state["held_back"] == 2
