"""Building blocks shared by every Triage schema.

The roadmap's "never invent" principle is enforced here, structurally rather
than by prompt instruction: a field that could not be determined is not an empty
string, it is an :class:`Unknown` carrying the reason it could not be filled.
Placeholder prose ("N/A", "TBD", a bare "unknown") is rejected by validation, so
a model cannot smuggle an empty answer past the schema.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# Strings that look like an answer but are not one. Compared case-insensitively
# against the stripped value, after trailing punctuation is removed.
_PLACEHOLDERS = frozenset(
    {
        "",
        "-",
        "--",
        "?",
        "n/a",
        "na",
        "nil",
        "none",
        "null",
        "tbd",
        "tba",
        "todo",
        "to be determined",
        "to be confirmed",
        "not applicable",
        "not available",
        "not determined",
        "unknown",
        "undetermined",
        "unspecified",
        "unclear",
        "pending",
        "xxx",
    }
)

_MIN_FILLED_LENGTH = 3


def reject_placeholder(value: str) -> str:
    """Reject prose that asserts nothing.

    A field that genuinely cannot be answered must be an :class:`Unknown` with a
    reason, or must say what is unknown *and why* in at least a sentence. Both
    are informative; "TBD" is not.
    """
    stripped = value.strip()
    normalised = stripped.rstrip(".!:;").strip().lower()
    if normalised in _PLACEHOLDERS:
        raise ValueError(
            f"{stripped!r} is a placeholder, not an answer. Use an Unknown with a "
            f"reason, or state explicitly what is not known and why."
        )
    if len(stripped) < _MIN_FILLED_LENGTH:
        raise ValueError(f"{stripped!r} is too short to be a meaningful answer.")
    return stripped


Filled: TypeAlias = Annotated[str, AfterValidator(reject_placeholder)]
"""A string that has been checked to actually say something."""


class Unknown(BaseModel):
    """An explicitly unfilled field.

    Serialises as ``{"unknown": true, "reason": "..."}``. The reason is
    mandatory: "we do not know" is only acceptable when accompanied by why.
    """

    model_config = ConfigDict(frozen=True)

    unknown: Literal[True] = True
    reason: Filled = Field(description="Why this could not be determined.")

    def __str__(self) -> str:
        return f"Unknown — {self.reason}"


MaybeUnknown: TypeAlias = Filled | Unknown
"""Either a real answer or an explicit, justified absence. Never empty."""


def is_unknown(value: MaybeUnknown) -> bool:
    return isinstance(value, Unknown)


def render(value: MaybeUnknown) -> str:
    """Human-readable form of a possibly-unknown field, for tickets and Slack."""
    return str(value) if isinstance(value, Unknown) else value


class Confidence(StrEnum):
    """Coarse confidence.

    Deliberately three levels rather than a percentage: a model asked for 0-1
    will happily produce 0.73, which reads as a measurement when it is a guess.
    See ADR-0002.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _CONFIDENCE_RANK[self]

    def at_least(self, threshold: Confidence) -> bool:
        return self.rank >= threshold.rank


_CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


class Feature(StrEnum):
    """Which feature produced a signal. Drives the confidence threshold."""

    F1 = "F1"
    F2 = "F2"
    F3 = "F3"


class TimeWindow(BaseModel):
    """The observation window a symptom was measured over."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end < self.start:
            raise ValueError("time window ends before it starts")
        return self

    def __str__(self) -> str:
        return f"{self.start.isoformat()} → {self.end.isoformat()}"
