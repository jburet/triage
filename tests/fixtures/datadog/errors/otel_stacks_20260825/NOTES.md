# The stack was there all along, in `custom.events`

Captured 2026-08-25 by `make capture-errors ARGS="--hours 24 --slug otel_stacks_20260825
--track trace --query 'env:prod'"`, read-only, against the real org over
`2026-08-24T08:23:54Z`–`2026-08-25T08:23:54Z`. `summary.md` is what the script measured;
this is what it means. Datadog retains spans about fifteen days, so this window cannot be
re-captured — treat these files the way
`tests/fixtures/datadog/hcl_software_uat_20260822/` is treated.

These payloads carry customer identifiers (tenant service names, queried entity ids,
connector codes). Nothing has been redacted: a fixture edited after the fact is no longer
a record of what the API returned.

## What it demonstrates

[ADR-0027](../../../../../docs/adr/0027-an-absence-datadog-discards-is-a-finding.md)
concluded there was no route from an Error Tracking issue to its occurrences. That was
wrong, and `spans_plt-merck-qa.json` is the counterexample. The platform runs the
**OpenTelemetry Java agent**, not Datadog's tracer, so:

- `@error.type:"<fqcn>"` matches nothing — the type is not in that attribute. All three
  rows of `_reconstruction.json` are `None` against issues claiming 199,529, 198,360 and
  119,550 occurrences.
- `@error.stack:*` returns zero org-wide — the stack is not in `error.stack`.
- `service:<svc> status:error` over **raw spans** returns the exceptions, with the type,
  the message and the **full stack including the `Caused by:` chain** inside
  `attributes.custom.events` — a **JSON-encoded string** holding an array of OTel span
  events, the one named `exception` being the one that matters.

`attributes.custom.span_events` beside it is a *count* (`"2"`), not the events.

## The hit rate, measured

| service | error spans | with an OTel stack | matching the issue's own type |
|---|---|---|---|
| `plt-merck-qa` | 20 | 20 | 0 |
| `plt-autostrade` | 20 | 6 | 0 |
| `plt-merck` | 20 | 20 | 0 |
| `plt-merck-dev` | 20 | 20 | 0 |

Both halves are the finding, and both are what the collector's statuses are derived from:

- **Evidence exists.** 66 of 80 error spans carry a complete OTel stack. The three
  `plt-merck*` captures are twenty stacks each of
  `zeenea.service.api.ScannerUpsertItemException` — 2,334 bytes, six frames, then a
  `Caused by: zeenea.commons.exceptions.TooBusyIndexingException` chain of twelve more,
  naming `ScannerService.scala:124`, `ScannerService.scala:194`, `LoadControl.scala:14`
  and `ZeeneaReferentielAppContext.scala:162`. Real files, real line numbers. Eleven of
  the eighteen frames are `io.opentelemetry.*` or `scala.*` and are not the application's
  code, which is why the frame filter exists.
- **It is often not the exception the issue is about.** The loudest issue for every one
  of these four services is `EntityNotFoundException`, and *none* of the retained error
  spans carries it. Retained error spans exist; this defect's were sampled away. That is
  a real absence and a different sentence from "nothing is collected here", which is what
  the four statuses now separate.

`plt-autostrade` is the third shape: 20 error spans, only 6 with an OTel stack — the other
14 are HTTP-status errors with no exception event at all.

## What replaces `synthetic_stack/`

`tests/fixtures/datadog/errors/synthetic_stack/` was hand-written because no real stack
was thought to exist, and its own notes said to delete it once one could be captured. This
is that capture, and it is deleted. Every stack-preserving rule (M8 behaviour 3.1) is now
tested against `spans_plt-merck-qa.json`.
