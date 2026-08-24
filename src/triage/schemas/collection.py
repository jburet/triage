"""What F1 collected, and what it asked for (ADR-0016).

Datadog is queried by Triage itself, so the shape of a collection has to record
more than the data: which collectors ran, which returned nothing, and — the
distinction that decides whether an absence is evidence — whether nothing came
back because the incident window is quiet or because the workload was never
instrumented. Those two produce the same empty response and opposite
conclusions, so they are different statuses here rather than the same empty list.

The reduction that gets a collector's answer down to prompt size happens before
these objects exist (``triage.collect.reduce``): a payload here is already the
short form, because a 176 KB log page is a token bill, not a fact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from triage.schemas.common import Filled, TimeWindow
from triage.schemas.hypothesis import CauseType


class AlertClass(StrEnum):
    """What kind of failure the alert describes. Decides the collector recipe.

    ``GENERIC`` is not a failure of classification, it is the honest answer for
    an alert that does not fit — and it still collects the sweep everything
    shares, so an unclassifiable alert is investigated rather than dropped.
    """

    CRASH_RESTART = "crash_restart"
    AVAILABILITY = "availability"
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    SATURATION = "saturation"
    GENERIC = "generic"


class Collector(StrEnum):
    """One deterministic query. The sweep runs a recipe's set; the follow-up loop
    may ask for any of them again with a different query, and nothing else."""

    MONITOR_QUERY = "monitor_query"
    MONITOR_DEFINITION = "monitor_definition"
    EVENTS_SERVICE = "events_service"
    EVENTS_NAMESPACE = "events_namespace"
    LOGS_AGGREGATE = "logs_aggregate"
    LOGS_SAMPLE = "logs_sample"
    METRICS = "metrics"
    SPANS = "spans"


class CollectorStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    """Nothing in the incident window, but the same query returns data when widened."""
    NOT_INSTRUMENTED = "not_instrumented"
    """Nothing in the window and nothing namespace-wide over seven days."""
    FAILED = "failed"
    SKIPPED = "skipped"
    """Not runnable at all: a monitor whose query has no re-runnable form, a scope
    the alert never carried."""


class CollectorResult(BaseModel):
    collector: Collector
    query: str
    status: CollectorStatus
    detail: str | None = Field(
        default=None, description="Why it failed, was skipped, or what was truncated."
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="Already reduced.")
    truncated: bool = False

    @property
    def has_data(self) -> bool:
        return self.status is CollectorStatus.OK


class FollowUpRequest(BaseModel):
    """One further call the ``analysis`` tier asked for.

    ``collector`` is a plain string rather than the enum on purpose: a model that
    invents a collector name must be *observably* discarded, and an enum field
    would make that a validation error inside the model call instead.
    """

    collector: str
    query: Filled
    why: Filled = Field(description="What this call would settle that the sweep did not.")


class FollowUpPlan(BaseModel):
    """What else to collect. Empty means the sweep already answers the question.

    There was a ``done`` flag beside this, and the model omitted it on every real
    alert: the prompt only had reason to mention it on the branch that asks for
    nothing, so a plan that *did* ask for calls never set it and the whole plan
    was rejected as invalid. It said nothing ``requests`` did not — the caller
    stopped on ``done or not requests`` — so the flag is gone rather than
    reinforced.
    """

    requests: list[FollowUpRequest] = Field(default_factory=list)


class Collection(BaseModel):
    """Everything F1 collected about one alert."""

    alert_class: AlertClass
    window: TimeWindow
    results: list[CollectorResult] = Field(default_factory=list)
    followup_calls: int = 0
    refused: list[str] = Field(
        default_factory=list,
        description="Follow-up requests that were not run, and why. Kept, never dropped.",
    )

    def by_collector(self, collector: Collector) -> list[CollectorResult]:
        return [result for result in self.results if result.collector is collector]

    @property
    def gaps(self) -> list[CollectorResult]:
        """Collectors whose emptiness means the workload is not instrumented."""
        return [
            result
            for result in self.results
            if result.status in (CollectorStatus.NOT_INSTRUMENTED, CollectorStatus.FAILED)
        ]

    def as_payload(self) -> dict[str, Any]:
        return {
            "alert_class": self.alert_class.value,
            "window": {"start": self.window.start, "end": self.window.end},
            "collectors": [result.model_dump(mode="json") for result in self.results],
            "refused_follow_ups": self.refused,
        }


class ProposedCause(BaseModel):
    """One candidate cause, as ``qualify`` is allowed to state it.

    There is no commit field. The deployed commit is resolved from the system map
    by the node, because a model that may write a commit will eventually write a
    plausible one (ADR-0016's "every fact comes from a call we made").
    """

    cause_type: CauseType
    service: Filled = Field(description="The service or workload the cause is in.")
    description: Filled = Field(description="The mechanism, in one or two sentences.")
    rank_score: float = Field(ge=0.0, le=1.0, description="Relative plausibility within the set.")


class Qualification(BaseModel):
    """Output of ``qualify``: what the telemetry shows, and what could explain it."""

    summary: Filled = Field(description="What the collected telemetry shows, without a cause.")
    # At least one. A qualification with no cause is not a qualification, and the
    # failure it hides is specific: asked for a summary and a list, a model once
    # answered with the whole list serialised as XML *inside* the summary. That
    # validated, produced no hypotheses, analysed nothing, and ended as a
    # low-confidence diagnosis — the expensive way to say the schema was too loose.
    causes: list[ProposedCause] = Field(min_length=1)


class AlertClassification(BaseModel):
    """Output of ``classify_alert``. The class, and nothing else (ADR-0016).

    The window is a rule and the collectors are a recipe, so this is the only
    judgement the ``triage`` tier makes about an alert.
    """

    alert_class: AlertClass
    reason: Filled = Field(description="What in the alert put it in that class.")
