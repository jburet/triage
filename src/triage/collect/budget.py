"""Keeping a collection inside the prompt budget it is allowed to spend (ADR-0016).

Every collector gets an equal share of ``max_prompt_bytes`` and keeps whatever it
can fit; what does not fit is dropped from the end of its longest list and *said
so* in the collector's own detail. Silent truncation is the failure mode worth
engineering against: a model shown 8 of 40 events with no note reasons about the
8 as though they were all of them, and writes a confident ticket about a window
it never saw.

An equal share rather than a priority order because the collectors carry
different halves of the same story — the reference incident needed the namespace
events *and* the logs *and* the diff — and a budget that starves one of them by
policy would have produced a wrong answer more cheaply.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, Self, TypeVar

from triage.schemas.collection import CollectorResult

TRUNCATABLE = ("events", "lines", "templates", "series", "buckets", "by")
"""Lists only, and ``stack`` is deliberately not one of them.

A stack trace is a string, so nothing here can shorten it, and that is the point:
the reduction that keeps a hundred repeated log lines affordable must not be the
thing that eats the one trace F2 exists to show (M8 3.1). A payload whose stack
alone overshoots its share overshoots — stated, like every other cut."""


class Fittable(Protocol):
    """A collection of collector results that can say how large it is.

    F1's :class:`~triage.schemas.collection.Collection` and F2's
    :class:`~triage.schemas.errors.ErrorCollection` carry different things around
    the same list, and the budget is about the list."""

    results: list[CollectorResult]

    def as_payload(self) -> dict[str, Any]: ...

    def model_copy(self, *, update: dict[str, Any]) -> Self: ...


FittableT = TypeVar("FittableT", bound=Fittable)


def _size(payload: dict[str, Any]) -> int:
    """Measured the way ``prompts.render`` will serialise it, indentation included."""
    return len(json.dumps(payload, default=str, indent=2).encode("utf-8"))


def _shrink(result: CollectorResult, share: int) -> CollectorResult:
    payload = dict(result.payload)
    dropped: list[str] = []
    for key in TRUNCATABLE:
        while _size(payload) > share and isinstance(payload.get(key), list) and payload[key]:
            items = list(payload[key])
            items.pop()
            payload[key] = items
            dropped.append(key)
    if not dropped:
        return result
    counts = {key: dropped.count(key) for key in dict.fromkeys(dropped)}
    note = ", ".join(f"{count} fewer {key}" for key, count in counts.items())
    detail = f"truncated to fit the prompt budget: {note}"
    return result.model_copy(
        update={
            "payload": payload,
            "truncated": True,
            "detail": f"{result.detail}; {detail}" if result.detail else detail,
        }
    )


ROUNDS = 6
MIN_SHARE = 64


def fit(collection: FittableT, max_bytes: int) -> FittableT:
    """The same collection, small enough to send, with every cut stated.

    An equal share is a first guess, not an answer: eleven collectors each holding
    their share still overshoot once the queries, statuses and details are counted,
    so the share halves until the whole thing fits. What it cannot shrink below is
    the collectors' own metadata — and a collection that is only metadata is still
    an honest one, because every dropped item is named in a detail.
    """
    if not collection.results or _size(collection.as_payload()) <= max_bytes:
        return collection
    share = max(max_bytes // len(collection.results), MIN_SHARE)
    fitted = collection
    for _ in range(ROUNDS):
        fitted = collection.model_copy(
            update={"results": [_shrink(result, share) for result in collection.results]}
        )
        if _size(fitted.as_payload()) <= max_bytes:
            return fitted
        share = max(share // 2, MIN_SHARE)
    return fitted
