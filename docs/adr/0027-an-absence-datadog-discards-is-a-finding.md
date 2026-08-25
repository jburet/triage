# 0027 — An absence Datadog is discarding is a finding, not a gap

Status: Proposed. The collectors and the `sampled_away` status exist in code (M8 Phase 3);
no report has been posted from them.

*Date: 2026-08-25*

## Decision

F2 collects the logs and the spans behind an error group **even where measurement says it
will find none**, and when it finds none it states *which* kind of nothing it found.

Three outcomes, not two:

- `not_instrumented` — the query is empty and so is the same window with the error predicate
  dropped. Nobody collects this signal for this scope. F1 already draws this
  ([ADR-0016](0016-datadog-collected-by-triage.md)).
- `sampled_away` — **new.** The query is empty, the group's own issue claims *N* occurrences
  inside that window, and the control query (the same services, no error predicate) returns
  data. The events happened, Datadog counted them, and they are being discarded before they
  can be searched.
- `ok` — evidence.

The collection also states the query it rebuilt from the group's own fields, **verbatim**,
alongside the count the issue claims. A report that says

> `service:(plt-systeme-u OR plt-systeme-u-rec) @error.type:"…EntityNotFoundException"`
> returned 0 spans; the issue counts 6,344 occurrences in the same window; the same window
> returns 211,179 spans for those services with the error predicate dropped

is a useful report. It is not evidence about the defect, but it is evidence about the
pipeline, and it names the one thing that would fix it.

F2 does **not** wait for evidence, does not retry, and does not refuse to run.

## Why

**Because the measurement is unambiguous and it is not about our query.** Probed against the
real org on 2026-08-25, over the reference hour `2026-08-25T04:35Z`–`05:35Z`:

| what was asked | answer |
|---|---|
| `service:plt-systeme-u-rec` (spans) | 211,179 in the hour, 3,576,641 over 7 days |
| `service:plt-systeme-u-rec status:error` | **0** in the hour, 48 over 7 days |
| `service:plt-systeme-u-rec @error.type:*` | **0** in the hour and over 7 days |
| `service:plt-systeme-u-rec @error.type:"…EntityNotFoundException"` | **0**, against 5,869 claimed |
| `@error.stack:*` across the whole org | **0 spans** |
| `sum:trace.services_by_operation.hits{service:plt-systeme-u-rec}` | 921,775 in the hour |
| `sum:trace.*.errors{service:plt-systeme-u-rec}` | **0**, every metric |
| logs for that service | 11 events in the hour, none at `status:error` |
| services shipping `@error.stack` logs, org-wide | 2, neither a tenant |

The service is instrumented and busy. The exceptions are real — Error Tracking counted
6,344, 5,869 and 4,009 of them in that hour. Nothing joins the two.

**Because there is no other route, and that was probed rather than assumed.** Nine candidate
Error Tracking endpoint shapes answered 404 (`…/issues/{id}/events`, `/latest-event`,
`/sample-event`, `/sample`, `/occurrences`); four candidate `include` values answered
400 `invalid include`. Datadog's own spec allows only `issue`, `issue.assignee`,
`issue.case`, `issue.team_owners`, and `IssueAttributes` carries no stack, no `trace_id` and
no `span_id`. Four candidate join facets (`@error.tracking.issue.id`, `@issue.id`,
`@error.fingerprint`, `issue_id`) returned zero spans over seven days; the only documented
join key is `error.fingerprint`, which the *application* must set and which Datadog never
echoes back. So issue → occurrence is a UI pivot, not an API.

**Because Datadog documents this exact failure.** "All errors are processed, but only
retained errors are available in the issue panel as an error sample… Spans associated with
the error need to be retained with a custom retention filter in order for samples of that
error to show up." The org's `GET /api/v2/apm/config/retention-filters` says its **"Error
Default" filter (`spans-errors-sampling-processor`, `status:error`, rate 1) is disabled**.
That is the switch. It is a Zeenea-side change, not a Triage-side one.

**Because "found nothing" and "is being thrown away" are opposite instructions to a
reader.** This is the same argument [ADR-0016](0016-datadog-collected-by-triage.md) makes for
`not_instrumented`, one step further along. An empty collector tells a developer to look
elsewhere. A collector that says the telemetry was counted and discarded tells an SRE to turn
a filter on — and until somebody is told, nobody will.

**Because the alternative is a feature that does not ship.** Blocking F2 on a Datadog
configuration change makes the value of the feature contingent on a ticket in another team's
backlog. F2's own input — the exception type, the message, the file and the function, present
on 202 of 202 issues over a week — is already enough for the report Phase 4 writes. The
collectors add evidence when it exists and name its absence when it does not; neither
outcome stops the pass.

**Because the rule is right where the data is missing.** The stack-preserving reduction
(3.1) is tested against a synthetic fixture, labelled as such, precisely because no real one
can be captured today. A rule that is untested is a guess; a rule that is tested against a
fixture nobody has seen in production is proven but unobserved, and those are different
claims. When the retention filter goes on, the collectors already work.

## Consequences

- `CollectorStatus` gains `sampled_away`, and F1 can produce it too — its span collector on a
  tenant with no retained error spans is the same situation.
- Each collector may cost one extra call: the control query that separates the two
  absences. It is run once per track per group, not once per collector.
- The reconstruction is layered — the narrow query (`@error.type`), then the broad one
  (`status:error`), then the control — and every one of the three is stated. Measured: the
  narrow shape returns nothing for these services over seven days and the broad one returns
  48, so the layering is not decoration.
- An F2 report in this org will, today, contain the exception, its message, its source
  location, its count and its tenants, plus a stated absence of logs and spans. It will not
  contain a stack trace. Phase 4's report must read well in that shape, because that is the
  shape it will have.
- Nothing here is a reason to relax [ADR-0014](0014-analysis-entrypoint-context-gather.md):
  a stated failure, never silence.

## Revisit when

The "Error Default" retention filter is enabled, or a custom one is written for `plt-*`
error spans. Then the narrow query starts answering, `sampled_away` should become rare, and
a run of ticks that still reports it means the filter is not matching what Error Tracking
groups — which is a different bug and a different fix.

Revisit sooner if Datadog ships an issue-sample or issue-id-on-span API. Both were probed on
2026-08-25 and neither exists; either would make the reconstruction obsolete rather than
merely lossy, and this ADR would be superseded rather than amended.

Revisit the whole shape if a team reads two of these reports and says the absence is noise.
The claim here is that naming a discarded signal is worth a paragraph in a report; that is a
judgement about readers, and readers can settle it.
