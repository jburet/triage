"""The exception inside an OpenTelemetry span, and the code it names (ADR-0029).

The platform runs the OpenTelemetry Java agent rather than Datadog's tracer, and
that decides where everything F2 wants lives. ``@error.type`` is empty,
``@error.stack:*`` returns zero spans across the org, and the exception — its
type, its message and its **whole stack including the ``Caused by:`` chain** — is
inside the span attribute ``custom.events``: a *JSON-encoded string* holding an
array of OTel span events, of which the one named ``exception`` is the one that
matters. (``custom.span_events`` beside it is a count, not the events.)

So the join from an Error Tracking issue to its occurrences is not a Datadog
attribute and never was. It is ``service:<svc> status:error`` over raw spans,
parsed here, and matched on ``exception.type``. Measured 2026-08-25 over 24 hours
at ``limit=20``: 66 of 80 error spans carried a complete stack.

Everything here is defensive on purpose. A span with no ``events``, a string that
is not JSON, an array of nulls, an exception event with no stack — each is a
shape the org returns and none of them may raise: a collector that dies on one
malformed span reports nothing about the nineteen beside it.

The frames are filtered because the runtime is not the application. Eleven of
the eighteen frames on the reference stack are ``io.opentelemetry.*`` or
``scala.*``, and pointing an analysis at the agent's own source is the same
failure M7 3.3 measured one level down.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EXCEPTION = "exception"

NOISE = (
    "io.opentelemetry.",
    "scala.",
    "java.",
    "javax.",
    "jdk.",
    "sun.",
    "com.sun.",
    "kotlin.",
)
"""Package prefixes that are the runtime rather than the code under investigation."""

_FRAME = re.compile(
    r"^\s*at (?:.+? @ )?(?P<declaring>[\w$./]+)\((?P<file>[\w$]+\.\w+):(?P<line>\d+)\)"
)
"""One printed frame. The ``name @ `` prefix is the OTel agent's async annotation,
and ``java.base/`` the JPMS module — both sit in front of the declaring class."""


@dataclass(frozen=True)
class Frame:
    """One application frame: where the code is, and what it was doing.

    ``path`` is package-relative — ``zeenea/service/api/ScannerService.scala`` —
    because that is what the stack actually says. Which module of the build holds
    it is resolved by suffix against the tree (ADR-0028 rule 2), not guessed here.
    """

    path: str
    file: str
    line: int
    declaring: str
    method: str

    @property
    def located(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class ExceptionEvent:
    """One ``exception`` span event, with enough of its span to open the trace."""

    error_type: str | None
    message: str | None
    stacktrace: str | None
    at: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    service: str | None = None
    operation: str | None = None
    resource: str | None = None

    @property
    def frames(self) -> tuple[Frame, ...]:
        return application_frames(self.stacktrace)


def _events(span: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    attributes = span.get("attributes")
    custom = attributes.get("custom") if isinstance(attributes, Mapping) else None
    raw = custom.get("events") if isinstance(custom, Mapping) else None
    if not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, Mapping)]


def span_exceptions(span: Mapping[str, Any]) -> list[ExceptionEvent]:
    """Every exception this span recorded. Never raises, whatever the span holds."""
    attributes = span.get("attributes")
    if not isinstance(attributes, Mapping):
        return []
    raw_custom = attributes.get("custom")
    custom: Mapping[str, Any] = raw_custom if isinstance(raw_custom, Mapping) else {}
    found = []
    for event in _events(span):
        if event.get("name") != EXCEPTION:
            continue
        fields = event.get("attributes")
        if not isinstance(fields, Mapping):
            continue
        found.append(
            ExceptionEvent(
                error_type=_text(fields.get("exception.type")),
                message=_text(fields.get("exception.message")),
                stacktrace=_text(fields.get("exception.stacktrace")),
                at=_text(attributes.get("start_timestamp")),
                trace_id=_text(attributes.get("trace_id")) or _text(custom.get("otel.trace_id")),
                span_id=_text(attributes.get("span_id")),
                service=_text(attributes.get("service")),
                operation=_text(attributes.get("operation_name")),
                resource=_text(attributes.get("resource_name")),
            )
        )
    return found


def exceptions_in(payload: Mapping[str, Any]) -> list[ExceptionEvent]:
    """Every exception in a ``POST /api/v2/spans/events/search`` answer, in its order."""
    data = payload.get("data")
    if not isinstance(data, Sequence) or isinstance(data, str | bytes):
        return []
    found: list[ExceptionEvent] = []
    for span in data:
        if isinstance(span, Mapping):
            found.extend(span_exceptions(span))
    return found


def application_frames(stacktrace: str | None) -> tuple[Frame, ...]:
    """The frames naming this codebase, in stack order, each line only once.

    A frame whose package cannot be read off the declaring class is dropped
    rather than guessed at: the rule is that the file name appears as a segment
    of the fully-qualified class, which is what makes the package the directory
    (ADR-0028 rule 1). ``Other.scala`` declared by ``com.acme.run`` breaks it, and
    a path invented from a broken frame is worse than one fewer frame.
    """
    if not stacktrace:
        return ()
    frames: list[Frame] = []
    seen: set[str] = set()
    for line in stacktrace.splitlines():
        match = _FRAME.match(line)
        if match is None:
            continue
        declaring = match.group("declaring").rsplit("/", 1)[-1]
        if declaring.startswith(NOISE):
            continue
        frame = _frame(declaring, match.group("file"), int(match.group("line")))
        if frame is None or frame.located in seen:
            continue
        seen.add(frame.located)
        frames.append(frame)
    return tuple(frames)


def _frame(declaring: str, file: str, line: int) -> Frame | None:
    segments = declaring.split(".")
    base = file.rsplit(".", 1)[0]
    for index, segment in enumerate(segments):
        if segment.split("$", 1)[0] != base:
            continue
        package = "/".join(segments[:index])
        return Frame(
            path=f"{package}/{file}" if package else file,
            file=file,
            line=line,
            declaring=declaring,
            method=".".join(segments[index + 1 :]),
        )
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
