"""How much is enough to spend an analysis on (ADR-0025).

ADR-0018 gates an alert on *duration*, because an alert fires and recovers and
"still failing fifteen minutes later" is the whole question. An Error Tracking
issue has no such shape: it does not recover, it accumulates. So the gate here
is volume, and it has three parts, corrected against a day of real ticks
(ADR-0025's revisit condition, met on 2026-08-25).

**A floor per tick**, and it is measured on the population it applies to: one
count per *group*, per *tick*, over issues that were new or regressed. Twenty-four
consecutive hourly ticks on 2026-08-25 brought eleven groups, arriving at 1, 1,
1, 2, 3, 4, 5, 30, 189, 7758 and 37691 occurrences. Nothing at all lands between
6 and 29, so every floor in that range decides the same day identically; ten
takes four of the eleven up on arrival. A floor set too low turns a team's
channel into an error stream, which is the failure mode ADR-0023 says to watch
for, and a floor of one would have posted five reports inside a single tick.

**A cumulative escalation, because the floor would otherwise be a cliff.** An
exception that happens four times an hour every hour is 96 times a day and never
crosses a per-tick floor of ten. It is fed by the occurrences that go on
happening rather than by new ones (ADR-0030), and that is what makes the floor a
delay rather than a drop: over the measured day every floor from 5 to 200
produced the same five reports, differing only in which hour each landed in. The
real case was not hypothetical — one group arrived with 4 occurrences, went on
to 186,242 in the same day, and was never new again.

**A per-tick cap, with the deferred groups named.** Five groups is what one tick
will take up; a sixth is deferred and said so, because a cap that drops the
overflow silently is a cap nobody can tell from a quiet hour. Five is what the
busiest tick of the measured day produced — a wave of some ninety new issues in
one hour, one defect arriving across dozens of tenants at once — and eighteen of
its twenty-four ticks produced none at all.

Everything below the gate is still *persisted with its count* — that is what the
escalation counts, and it is the common outcome, so a tick reports how many it
held back or it looks like a pass that found nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from triage.config import ErrorsConfig
from triage.schemas.errors import ErrorGroup, Novelty


class GateOutcome(StrEnum):
    """What one tick decided about one group."""

    ANALYSE = "analyse"
    HELD_BACK = "held_back"
    """Below the floor and below the escalation. Persisted with its count."""
    DEFERRED = "deferred"
    """Would have been analysed; the per-tick cap took the louder ones first."""
    SETTLED = "settled"
    """Already reported, and nothing has happened since that changes the report."""
    UNMAPPED = "unmapped"
    """No repository resolves for it, so there is no tree to read (ADR-0026)."""


@dataclass(frozen=True)
class GateDecision:
    """One group, what the tick decided, and the sentence that says why."""

    group: ErrorGroup
    outcome: GateOutcome
    reason: str

    @property
    def analysed(self) -> bool:
        return self.outcome is GateOutcome.ANALYSE


def gate(groups: Sequence[ErrorGroup], config: ErrorsConfig, now: datetime) -> list[GateDecision]:
    """Decide every group, loudest first, and cap how many one tick takes up.

    The groups must carry the cumulative counts the repository keeps: the whole
    point of the escalation is that it reads a total no single tick can see.
    """
    ordered = sorted(groups, key=lambda group: (-group.occurrences, group.key))
    decisions: list[GateDecision] = []
    taken = 0
    for group in ordered:
        outcome, reason = _decide(group, config, now)
        if outcome is GateOutcome.ANALYSE:
            if taken >= config.max_groups_per_tick:
                outcome = GateOutcome.DEFERRED
                reason = (
                    f"{reason}, but this tick had already taken up "
                    f"{config.max_groups_per_tick} groups — it waits for the next one"
                )
            else:
                taken += 1
        decisions.append(GateDecision(group=group, outcome=outcome, reason=reason))
    return decisions


def held_back(decisions: Sequence[GateDecision]) -> int:
    """How many groups the volume gate kept. The number a tick has to report."""
    return sum(1 for decision in decisions if decision.outcome is GateOutcome.HELD_BACK)


def _decide(group: ErrorGroup, config: ErrorsConfig, now: datetime) -> tuple[GateOutcome, str]:
    if not group.analysable:
        return GateOutcome.UNMAPPED, group.unanalysable_reason or "no repository resolves for it"
    if group.analysis_count:
        return _again(group, config, now)
    if group.novelty is Novelty.CONTINUING:
        return _seen_again(group, config)
    if group.occurrences >= config.min_occurrences:
        return (
            GateOutcome.ANALYSE,
            f"{group.occurrences} occurrences this tick, at or above the floor of "
            f"{config.min_occurrences}",
        )
    if group.cumulative_occurrences >= config.cumulative_occurrences:
        return (
            GateOutcome.ANALYSE,
            f"{group.occurrences} occurrences this tick is below the floor of "
            f"{config.min_occurrences}, but {group.cumulative_occurrences} in total has "
            f"crossed the escalation threshold of {config.cumulative_occurrences} — a slow "
            f"bleed nobody would otherwise see",
        )
    return (
        GateOutcome.HELD_BACK,
        f"{group.occurrences} occurrences this tick and {group.cumulative_occurrences} in "
        f"total, below both the floor of {config.min_occurrences} and the escalation "
        f"threshold of {config.cumulative_occurrences}",
    )


def _seen_again(group: ErrorGroup, config: ErrorsConfig) -> tuple[GateOutcome, str]:
    """A group that has never been reported and was not new or regressed this tick.

    The floor is deliberately out of reach here. ADR-0025 says an issue that is
    neither new nor regressed produces no report, and a tick that merely counted
    a group again has not seen it arrive; what it did is move the total. So the
    escalation — a statement about the group's whole life — is the only door
    open, which is exactly the slow bleed ADR-0030 made reachable.
    """
    if group.cumulative_occurrences >= config.cumulative_occurrences:
        return (
            GateOutcome.ANALYSE,
            f"{group.occurrences} more occurrences this tick, neither new nor regressed, so "
            f"the floor does not apply — but {group.cumulative_occurrences} in total has "
            f"crossed the escalation threshold of {config.cumulative_occurrences}: a bleed no "
            f"single tick is large enough to show",
        )
    return (
        GateOutcome.HELD_BACK,
        f"{group.occurrences} more occurrences this tick, neither new nor regressed, and "
        f"{group.cumulative_occurrences} in total — below the escalation threshold of "
        f"{config.cumulative_occurrences}, and the floor of {config.min_occurrences} applies "
        f"only to occurrences that arrived new or regressed",
    )


def _again(group: ErrorGroup, config: ErrorsConfig, now: datetime) -> tuple[GateOutcome, str]:
    """Why a group that has already been reported is worth reporting a second time.

    A regression reopens it at once and on its own: a fix that did not hold is
    news the moment it happens, and no cooldown should sit on it.

    Everything else waits for ``reanalyse_after``, and that gate is measured
    rather than cautious. The loudest group of the reference hour did 10,763
    occurrences in it; against a cumulative threshold of a hundred it crosses the
    next escalation interval on *every* tick, for ever. A rule that only counted
    would therefore repost the same defect hourly, which is precisely the error
    stream ADR-0023 says to watch for. So the escalation says *whether* there is
    more to say and the cooldown says *when* it may be said.

    Past the cooldown, two things earn a second report: another whole escalation
    threshold bled since the last one — counted from where that left it, not from
    zero — or a group that is simply still going at the floor a week later.
    """
    ordinal = group.analysis_count + 1
    if group.novelty is Novelty.REGRESSED:
        return (
            GateOutcome.ANALYSE,
            f"regressed since it was last reported, which makes this report number {ordinal}",
        )
    next_interval = group.analysed_at_cumulative + config.cumulative_occurrences
    cooldown = timedelta(hours=config.reanalyse_after)
    if group.last_analysed_at is not None and now - group.last_analysed_at < cooldown:
        return (
            GateOutcome.SETTLED,
            f"reported {group.analysis_count} time(s), most recently "
            f"{group.last_analysed_at.isoformat()} — under the {config.reanalyse_after} hours "
            f"before a group that has not regressed is looked at again",
        )
    if group.cumulative_occurrences >= next_interval:
        return (
            GateOutcome.ANALYSE,
            f"{group.cumulative_occurrences} in total has crossed the next escalation "
            f"interval at {next_interval}; this is report number {ordinal}",
        )
    if group.occurrences >= config.min_occurrences:
        return (
            GateOutcome.ANALYSE,
            f"still at {group.occurrences} occurrences a tick {config.reanalyse_after} hours "
            f"after it was last reported; this is report number {ordinal}",
        )
    return (
        GateOutcome.SETTLED,
        f"already reported {group.analysis_count} time(s); it has not regressed, its "
        f"{group.cumulative_occurrences} total has not reached the next escalation "
        f"interval at {next_interval}, and {group.occurrences} this tick is below the "
        f"floor of {config.min_occurrences}",
    )
