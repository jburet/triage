"""Flapping: the half of the persistence gate that is not a filter (ADR-0018).

The gate exists because across 961 measured pod-down cycles the longest was nine
minutes, and analysing each of them would have completed after the condition it
described had gone. But silently dropping every short cycle would hide a real
defect behind the mechanism that protects us from it: one dev tenant fired every
32 minutes, for five minutes at a time, for a day — and the incident that
motivated the whole design, a liveness probe shorter than the pod's own startup,
has exactly that signature. A design that only analysed long outages would never
have found it.

So the recoveries are counted, and enough of them for one monitor and group is a
*finding* — an infrastructure diagnosis about the workload or the monitor, put
through the same ticket pipeline as anything else. Deliberately not an incident:
nothing here is on fire, and the ticket says what to change rather than what to
restore.

The diagnosis is built by rule, not by a model call. Everything in it is counted
— how many cycles, how long each lasted, over what window — and a model asked to
narrate a counter would only add a way to be wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from triage.config import Config
from triage.runtime import DEPS_KEY, Deps
from triage.schemas.common import Confidence, Feature, TimeWindow, Unknown
from triage.schemas.diagnosis import (
    AcceptanceCriterion,
    Diagnosis,
    Evidence,
    EvidenceKind,
    Impact,
    Location,
    OpenQuestion,
    Symptom,
)
from triage.schemas.signal import Signal, SignalStatus

FLAP_MARKER = "flap_reported"
"""Set on the cycles a flap ticket already counted, so the next one starts from zero."""


def _minutes(seconds: float | None) -> float:
    return round((seconds or 0) / 60, 1)


def flapping_diagnosis(cycles: list[Signal], config: Config) -> Diagnosis:
    """One counted fact, in the shape the ticket pipeline consumes."""
    ordered = sorted(cycles, key=lambda cycle: cycle.fired_at or datetime.min)
    first, last = ordered[0], ordered[-1]
    durations = [_minutes(cycle.duration_seconds) for cycle in ordered]
    window = TimeWindow(
        start=first.fired_at or first.received_at,
        end=last.recovered_at or last.fired_at or last.received_at,
    )
    monitor = f"monitor {first.monitor_id}" + (f" group {first.group}" if first.group else "")

    return Diagnosis(
        feature=Feature.F1,
        service=first.service,
        team=first.team or "",
        symptom=Symptom(
            description=(
                f"{len(ordered)} alert cycles on {monitor} in "
                f"{config.thresholds.flap_window_hours} hours, each recovering on its own: "
                f"longest {max(durations)} min, median {median(durations)} min. None lasted "
                f"the {config.thresholds.alert_persistence_minutes} minutes that would have "
                f"had it analysed as an incident."
            ),
            window=window,
        ),
        impact=Impact(
            users=Unknown(
                reason="every cycle recovered before the persistence gate, so no user "
                "impact was measured for any of them"
            ),
            services=[first.service],
            slos=Unknown(
                reason="the cycles were not analysed individually, so no SLO was evaluated"
            ),
        ),
        probable_cause=(
            "The workload fails and recovers repeatedly — a restart loop, a probe it cannot "
            "pass under some condition, or a resource it briefly exhausts — or the monitor "
            "is more sensitive than the condition it watches. Either way this is a "
            "configuration defect in the workload or its monitor, not an incident."
        ),
        confidence=Confidence.MEDIUM,
        confidence_rationale=(
            "The recurrence is counted rather than inferred, so that the pattern is real is "
            "not in doubt. Which of the two mechanisms produces it has not been analysed, "
            "because no single cycle lasted long enough to be — hence medium and not high."
        ),
        evidence=[
            Evidence(
                kind=EvidenceKind.OTHER,
                description=(
                    f"{len(ordered)} self-recovered cycles between {window.start.isoformat()} "
                    f"and {window.end.isoformat()}, all for {monitor}."
                ),
            ),
            Evidence(
                kind=EvidenceKind.METRIC,
                description=f"Cycle durations, in minutes: {', '.join(str(d) for d in durations)}.",
            ),
        ],
        location=Location(
            repo=Unknown(
                reason="a flapping pattern is not located in code until one cycle is analysed"
            ),
            commit=Unknown(reason="no single cycle was analysed, so no deployed commit applies"),
        ),
        expected_change=AcceptanceCriterion(
            statement=(
                f"Fewer than {config.thresholds.flap_count} self-recovering cycles for "
                f"{monitor} over {config.thresholds.flap_window_hours} hours — by fixing the "
                f"workload, or by making the monitor describe the condition it means."
            ),
            how_to_verify="The monitor's own event history in Datadog, over the same window.",
        ),
        out_of_scope=[
            "Silencing or downtiming the monitor without deciding which of the two causes it is."
        ],
        unknowns=[
            OpenQuestion(
                question="Is the workload unstable, or is the monitor too sensitive?",
                why_unresolved=(
                    "No cycle persisted long enough to be analysed, so no telemetry was "
                    "collected for any of them individually."
                ),
            )
        ],
    )


def countable(cycles: list[Signal], config: Config, now: datetime) -> list[Signal]:
    """Self-recovered cycles inside the window that no earlier flap ticket counted."""
    since = now - timedelta(hours=config.thresholds.flap_window_hours)
    return [
        cycle
        for cycle in cycles
        if cycle.status is SignalStatus.SELF_RECOVERED
        and not cycle.payload.get(FLAP_MARKER)
        and (cycle.fired_at or cycle.received_at) >= since
    ]


async def report_flapping(
    deps: Deps, config: Config, now: datetime, recovered: list[Signal]
) -> list[str]:
    """Raise one ticket per flapping (monitor, group), then reset that pair's counter."""
    from triage.graphs.ticket_pipeline import graph as ticket_pipeline

    reported: list[str] = []
    seen: set[tuple[int | None, str | None]] = set()
    for signal in recovered:
        key = (signal.monitor_id, signal.group)
        if key in seen:
            continue
        seen.add(key)
        cycles = countable(await deps.repo.signals_for_cycle(*key), config, now)
        if len(cycles) < config.thresholds.flap_count:
            continue

        await ticket_pipeline.ainvoke(
            {"diagnosis": flapping_diagnosis(cycles, config)},
            config={"configurable": {DEPS_KEY: deps}},
        )
        for cycle in cycles:
            await deps.repo.update_signal(
                cycle.model_copy(update={"payload": {**cycle.payload, FLAP_MARKER: True}})
            )
        reported.append(f"{signal.monitor_id}:{signal.group or ''}")
    return reported
