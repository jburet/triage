# 0029 — The exception is in the OpenTelemetry span events, not in `@error.type`

Status: Proposed. Supersedes [ADR-0027](0027-an-absence-datadog-discards-is-a-finding.md)
in its central claim. `triage.errors.otel`, the join in `triage.errors.sweep` and the
observed-frame rule in `triage.errors.paths` exist in code (M8 Phase 3, corrected); no
report has been posted from them.

*Date: 2026-08-25*

## Decision

There **is** a route from an Error Tracking issue to its occurrences. F2 takes it.

The query is `service:<svc> status:error` over **raw spans**, and the join to the group is
`exception.type` inside the span attribute **`custom.events`** — a JSON-encoded string
holding an array of OpenTelemetry span events, of which the one named `exception` carries
the type, the message and the **whole stack trace including its `Caused by:` chain**. The
match is made in Triage, over parsed events, because it cannot be made in the query.

Both halves are stated verbatim in the `Reconstruction` — the query that was sent and the
value the answers were filtered on — because a reader who cannot re-run the search cannot
check the finding.

Four outcomes replace ADR-0027's three, and the control query still separates the last two:

- **`ok`** — a retained error span carries this exception. The collection gains an
  `ExceptionExemplar`: the stack, its application frames as `path:line`, the trace id, the
  operation and the timestamp.
- **`sampled_away`, this defect** — error spans *were* retained for these services and none
  of them carries this exception. The detail names the types that were retained instead.
- **`sampled_away`, the whole track** — no error span at all was retained, though the
  control query shows the services are alive. ADR-0027's measured case.
- **`not_instrumented`** — the control is dead too. Nobody collects this signal here.

And the frames a real stack names are the paths an analysis opens, ahead of the class-name
conversion [ADR-0028](0028-a-class-name-is-not-a-path.md) builds. That conversion stays, as
the fallback for a group with no retained stack, and the report says which of the two it
had — an observed `ScannerService.scala:124` and a manufactured
`src/main/scala/…/ScannerService.scala` must not read alike.

## Why

**Because ADR-0027 measured the right thing through the wrong attribute.** Its every number
still holds: `@error.type:"<fqcn>"` returns nothing, `@error.type:*` returns nothing,
`@error.stack:*` returns zero spans org-wide. What it concluded from them — that the
occurrences are unreachable — was wrong, and the reason is one fact nobody had: **the
platform runs the OpenTelemetry Java agent, not Datadog's tracer**. Under OTel the exception
is a *span event*, and Datadog surfaces span events as a JSON string in `custom.events`
rather than as the `error.*` attributes its own tracer sets. Zero results from `@error.type`
is not evidence about retention. It is evidence about which agent is running, and ADR-0027
read it as the first.

**Because the evidence is there, and it is exactly what F2 was built to deliver.** Measured
live on 2026-08-25 over 24 hours at `limit=20`
(`tests/fixtures/datadog/errors/otel_stacks_20260825/`):

| service | error spans | with an OTel stack | matching the issue's own type |
|---|---|---|---|
| `plt-merck-qa` | 20 | 20 | 0 |
| `plt-merck` | 20 | 20 | 0 |
| `plt-merck-dev` | 20 | 20 | 0 |
| `plt-autostrade` | 20 | 6 | 0 |
| `plt-bred` | 20 | 7 | 5 |
| `plt-gema-uat` | 8 | 7 | 0 |

The first four rows are the capture and are replayed by the tests; the last two were probed
by hand the same day and are recorded here only. 66 of the capture's 80 spans carry a
complete stack. The reference one is 2,334 bytes,
six frames, then a `Caused by: zeenea.commons.exceptions.TooBusyIndexingException` chain of
twelve more, naming `ScannerService.scala:124`, `ScannerService.scala:194`,
`LoadControl.scala:14` and `ZeeneaReferentielAppContext.scala:162` — real files, real line
numbers, in a repository Triage can clone.

**Because the mismatch column is a finding too, and a sharper one than ADR-0027's.** The
loudest issue for four of these services is `EntityNotFoundException`, and *none* of their
retained error spans carries it. That is not "nothing is collected here": it is a sampler
that kept error spans for the service and threw this defect's away. ADR-0027's report called
those spans "unrelated noise (profiler rate-limit errors, `http.client` spans)" — they were
never unrelated, nothing was matching them against the group because the matching attribute
was wrong.

**Because the join has to be ours, not Datadog's.** No `exception.type` facet exists to
filter on in the query; `custom.events` is a string, and Datadog will not search inside it.
So the filter is a rule in Python over the twenty spans a page returns. The cost is real and
bounded: the query over-fetches, and a service whose error spans are dominated by another
exception can bury this one past `limit`. Both are stated in the collector's detail —
"20 of the 20 error spans Datadog retained carry this exception" is a sentence that tells a
reader when they are looking at a sample.

**Because a stack is what makes the analysis worth running.** [ADR-0028](0028-a-class-name-is-not-a-path.md)
builds a path out of a class name by convention, and its own "revisit when" says the first
real run is what tells whether the convention holds. A frame does not need the convention:
it *is* the path, with the line. Preferring it is not a new principle, it is ADR-0019's and
ADR-0020's applied one axis over — what was observed beats what was derived, and the
difference is printed.

**Because the retention filter is still off, and that still matters.** ADR-0027's
recommendation — enable the org's disabled "Error Default" filter
(`spans-errors-sampling-processor`, `status:error`, rate 1) — is unchanged and is what would
move the mismatch column from 0 to most rows. What changes is that F2 no longer needs it to
be worth running.

## Consequences

- `Reconstruction` loses `narrow`/`broad` and gains `query`/`match`. The layered
  narrow-then-broad attempt is gone: there was never anything for the narrow query to
  return, so the layering was two calls to learn nothing. One call per collector, plus the
  control on an empty one.
- `ErrorCollection` gains `exemplar`, and the report gains a stack block in Evidence and a
  `*Stack frames:*` line in Location. Both are bounded for Slack: the stack is cut *per
  `Caused by:` chapter*, six frames each, so the cause survives a cut that a head-and-tail
  trim would have eaten.
- The span search over-fetches by design. It reads whatever the page returns and discards
  most of it, which is one call at 300 requests an hour against a gate of two concurrent —
  affordable, and the reason the reduction states how many it kept of how many it saw.
- `tests/fixtures/datadog/errors/synthetic_stack/` is deleted. It existed because no real
  stack was thought to exist; the stack-preserving rule (M8 3.1) is now tested against a
  captured one, which is a stronger claim than the one its own notes said it could make.
- `SAMPLED_AWAY` now covers two different sentences and is told apart by its detail rather
  than by a fifth enum member. A status is what a reader scans; the detail is what they act
  on, and inventing `sampled_away_partially` would put the distinction in the half nobody
  reads aloud.
- ADR-0028 is not superseded. Its conversion is still the only thing a group with no
  retained stack has, which — on this org's mismatch rate — is most groups.

## Revisit when

**The mismatch column moves.** If the "Error Default" retention filter is enabled and the
matching count is still 0 across a week of ticks, then the sampler is not the explanation and
something else is dropping these spans — a different bug, and one this ADR's four statuses
would be mislabelling.

**The platform stops running the OpenTelemetry agent, or Datadog starts indexing span
events.** Either makes `custom.events` the wrong place to look: the first moves the exception
into `error.type`/`error.stack` and makes the parsing dead code, the second moves the match
back into the query and makes it a facet. Both are improvements and both make this ADR wrong
in its mechanism rather than its conclusion.

**A frame is ever shown to point at the wrong file.** The path is read off the declaring
class by finding the file's own name as one of its segments; a build that renames classes, or
a language whose frames do not print that way, breaks it silently. Nothing has yet opened one
of these paths in a real tree — the investigative kinds still have no image (M7 3.4) — so the
first analysis that clones a repository is what settles whether an observed frame is worth
what this ADR claims for it.
