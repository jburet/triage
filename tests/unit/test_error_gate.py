"""The volume gate, on the counts the reference hour actually produced (M8 2.3-2.6).

``6344, 5869, 4009, 850, 835, 650, 435, 200, 29, 15, 4, 2, 2, 2, 1`` are the
occurrences per issue measured over one hour of the org on 2026-08-25. They are
what ``min_occurrences: 10`` was chosen against, so they are what the floor is
tested against — five of the fifteen fall below it.
"""

from datetime import UTC, datetime, timedelta

import pytest

from triage.config import ErrorsConfig
from triage.errors.gate import GateOutcome, gate, held_back
from triage.schemas.errors import ErrorGroup, ErrorGroupStatus, ErrorTrack, Novelty

NOW = datetime(2026, 8, 25, 5, 35, tzinfo=UTC)
MEASURED = [6344, 5869, 4009, 850, 835, 650, 435, 200, 29, 15, 4, 2, 2, 2, 1]


def a_group(occurrences: int = 1, **overrides: object) -> ErrorGroup:
    base: dict[str, object] = {
        "key": f"NullPointerException|Property.scala|get|platform#{occurrences}",
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
        "cumulative_occurrences": occurrences,
        "first_seen": NOW - timedelta(hours=1),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


def reported(**overrides: object) -> ErrorGroup:
    """A group that has already been taken up once."""
    base: dict[str, object] = {
        "status": ErrorGroupStatus.REPORTED,
        "analysis_count": 1,
        "analysed_at_cumulative": 120,
        "cumulative_occurrences": 120,
        "last_analysed_at": NOW - timedelta(hours=200),
        "thread_ts": "1756100000.000100",
        "first_report_url": "https://slack/archives/C1/p1756100000000100",
    }
    base.update(overrides)
    return a_group(int(base.pop("occurrences", 5)), **base)  # type: ignore[arg-type]


@pytest.fixture
def errors() -> ErrorsConfig:
    return ErrorsConfig()


class TestTheFloor:
    """2.3 — below the floor is persisted with its count and analysed nothing."""

    def test_the_measured_hour_holds_back_a_third_of_its_issues(self, errors: ErrorsConfig) -> None:
        groups = [a_group(count) for count in MEASURED]

        decisions = gate(groups, errors, NOW)

        assert held_back(decisions) == 5
        assert sorted(
            decision.group.occurrences
            for decision in decisions
            if decision.outcome is GateOutcome.HELD_BACK
        ) == [1, 2, 2, 2, 4]

    def test_a_group_exactly_at_the_floor_clears_it(self, errors: ErrorsConfig) -> None:
        decisions = gate([a_group(errors.min_occurrences)], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE

    def test_a_held_back_group_says_both_numbers_it_missed(self, errors: ErrorsConfig) -> None:
        decisions = gate([a_group(4)], errors, NOW)

        assert "4 occurrences this tick" in decisions[0].reason
        assert "10" in decisions[0].reason
        assert "100" in decisions[0].reason


class TestTheSlowBleed:
    """2.4 — never clears the floor, still gets seen."""

    def test_a_cumulative_count_past_the_threshold_is_analysed(self, errors: ErrorsConfig) -> None:
        """Four an hour, every hour: 96 a day and never ten in one tick."""
        decisions = gate([a_group(4, cumulative_occurrences=100)], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE
        assert "slow bleed" in decisions[0].reason

    def test_one_short_of_the_threshold_is_still_held_back(self, errors: ErrorsConfig) -> None:
        decisions = gate([a_group(4, cumulative_occurrences=99)], errors, NOW)

        assert decisions[0].outcome is GateOutcome.HELD_BACK


class TestNotTwice:
    """2.5 — an already-reported group is quiet until something changes."""

    def test_the_same_news_again_is_not_reported(self, errors: ErrorsConfig) -> None:
        decisions = gate([reported()], errors, NOW)

        assert decisions[0].outcome is GateOutcome.SETTLED

    def test_a_regression_reopens_it_at_once(self, errors: ErrorsConfig) -> None:
        """No cooldown sits on a fix that did not hold."""
        fresh = reported(novelty=Novelty.REGRESSED, last_analysed_at=NOW - timedelta(hours=1))

        decisions = gate([fresh], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE
        assert "regressed" in decisions[0].reason

    def test_the_cooldown_holds_the_escalation_path(self, errors: ErrorsConfig) -> None:
        """Measured: the loudest group of the reference hour crosses any interval every tick.

        10,763 occurrences an hour against a cumulative threshold of a hundred
        would repost the same defect hourly for ever — the error stream ADR-0023
        says to watch for. The escalation says whether there is more to say; the
        cooldown says when it may be said.
        """
        loud = reported(
            occurrences=10763,
            cumulative_occurrences=21526,
            analysed_at_cumulative=10763,
            last_analysed_at=NOW - timedelta(hours=1),
        )

        decisions = gate([loud], errors, NOW)

        assert decisions[0].outcome is GateOutcome.SETTLED
        assert str(errors.reanalyse_after) in decisions[0].reason

    def test_crossing_the_next_escalation_interval_reopens_it(self, errors: ErrorsConfig) -> None:
        """Counted from where it was last taken up, not from zero."""
        decisions = gate([reported(cumulative_occurrences=220)], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE
        assert "220" in decisions[0].reason

    def test_but_not_before_a_whole_interval_has_passed(self, errors: ErrorsConfig) -> None:
        decisions = gate([reported(cumulative_occurrences=219)], errors, NOW)

        assert decisions[0].outcome is GateOutcome.SETTLED

    def test_the_reason_says_which_occurrence_the_next_report_is(
        self, errors: ErrorsConfig
    ) -> None:
        decisions = gate([reported(analysis_count=3, novelty=Novelty.REGRESSED)], errors, NOW)

        assert "number 4" in decisions[0].reason

    def test_the_group_still_carries_the_thread_the_first_report_opened(
        self, errors: ErrorsConfig
    ) -> None:
        """2.5 and 4.5 need the second report to link the first, not start a fifth thread."""
        decisions = gate([reported(novelty=Novelty.REGRESSED)], errors, NOW)

        assert decisions[0].group.thread_ts == "1756100000.000100"
        assert decisions[0].group.first_report_url is not None

    def test_a_loud_group_is_looked_at_again_once_the_cooldown_elapses(
        self, errors: ErrorsConfig
    ) -> None:
        stale = reported(
            occurrences=50, last_analysed_at=NOW - timedelta(hours=errors.reanalyse_after + 1)
        )

        decisions = gate([stale], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE
        assert str(errors.reanalyse_after) in decisions[0].reason


class TestTheCap:
    """2.6 — at most N a tick, loudest first, and the rest named."""

    def test_only_the_cap_is_taken_up(self, errors: ErrorsConfig) -> None:
        decisions = gate([a_group(count) for count in MEASURED], errors, NOW)

        analysed = [d for d in decisions if d.outcome is GateOutcome.ANALYSE]
        assert len(analysed) == errors.max_groups_per_tick

    def test_the_loudest_are_the_ones_taken(self, errors: ErrorsConfig) -> None:
        decisions = gate([a_group(count) for count in MEASURED], errors, NOW)

        analysed = [d.group.occurrences for d in decisions if d.outcome is GateOutcome.ANALYSE]
        assert analysed == [6344, 5869, 4009, 850, 835]

    def test_the_overflow_is_deferred_and_named_rather_than_dropped(
        self, errors: ErrorsConfig
    ) -> None:
        decisions = gate([a_group(count) for count in MEASURED], errors, NOW)

        deferred = [d for d in decisions if d.outcome is GateOutcome.DEFERRED]
        assert [d.group.occurrences for d in deferred] == [650, 435, 200, 29, 15]
        assert all(d.group.key for d in deferred)
        assert "waits for the next one" in deferred[0].reason

    def test_every_group_gets_a_decision(self, errors: ErrorsConfig) -> None:
        decisions = gate([a_group(count) for count in MEASURED], errors, NOW)

        assert len(decisions) == len(MEASURED)

    def test_a_group_held_back_does_not_spend_the_cap(self, errors: ErrorsConfig) -> None:
        """The cap counts analyses, not groups: a quiet group must not crowd a loud one out."""
        groups = [a_group(1) for _ in range(20)] + [a_group(500)]

        decisions = gate(groups, errors, NOW)

        assert [d.outcome for d in decisions if d.group.occurrences == 500] == [GateOutcome.ANALYSE]


class TestNoTreeToRead:
    """2.2's other half, seen by the gate: reported, never analysed."""

    def test_an_unmapped_group_is_never_analysed_however_loud(self, errors: ErrorsConfig) -> None:
        orphan = a_group(9999, repository=None, unanalysable_reason="no repository runs orphan-a")

        decisions = gate([orphan], errors, NOW)

        assert decisions[0].outcome is GateOutcome.UNMAPPED
        assert decisions[0].reason == "no repository runs orphan-a"

    def test_and_it_does_not_spend_the_cap_either(self, errors: ErrorsConfig) -> None:
        orphan = a_group(9999, repository=None)
        groups = [orphan] + [a_group(count) for count in MEASURED[:5]]

        decisions = gate(groups, errors, NOW)

        analysed = [d for d in decisions if d.outcome is GateOutcome.ANALYSE]
        assert len(analysed) == 5


def test_an_empty_tick_decides_nothing(errors: ErrorsConfig) -> None:
    """The common case: no issue in the reference hour was new or regressed."""
    assert gate([], errors, NOW) == []
    assert held_back([]) == 0


class TestSeenAgainButNotNew:
    """The escalation's material: a tick that only re-counted a group it already knew.

    Datadog marks an issue new exactly once, so a group held back below the floor
    is never *new* again — which is why the escalation had to be fed from the
    issues that merely went on occurring (ADR-0030). What such a tick may do is
    bounded: it moves the total, and the total is the only thing that can promote
    the group.
    """

    def test_a_group_only_seen_again_cannot_clear_the_floor(self, errors: ErrorsConfig) -> None:
        """Refreshing a count is not reporting. ADR-0025's rule, kept."""
        seen = a_group(30, novelty=Novelty.CONTINUING, cumulative_occurrences=34)

        decisions = gate([seen], errors, NOW)

        assert decisions[0].outcome is GateOutcome.HELD_BACK
        assert "neither new nor regressed" in decisions[0].reason

    def test_but_the_total_it_moved_still_escalates(self, errors: ErrorsConfig) -> None:
        """2.4, for the first time truthfully: four an hour, new in one tick only."""
        bleeding = a_group(4, novelty=Novelty.CONTINUING, cumulative_occurrences=100)

        decisions = gate([bleeding], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE
        assert "100 in total" in decisions[0].reason

    def test_an_already_reported_group_is_still_going_on_its_continuing_count(
        self, errors: ErrorsConfig
    ) -> None:
        """The other clause the same defect killed: "still at N a tick a week later"."""
        stale = reported(
            occurrences=50,
            novelty=Novelty.CONTINUING,
            last_analysed_at=NOW - timedelta(hours=errors.reanalyse_after + 1),
        )

        decisions = gate([stale], errors, NOW)

        assert decisions[0].outcome is GateOutcome.ANALYSE
        assert "still at 50" in decisions[0].reason
