# Plan: M8 — F2, a recurring code exception becomes a report (2026-08-25)

A second input beside F1. Every hour Triage asks Datadog Error Tracking which **code
exceptions** are new or have regressed, groups the ones that are the same defect seen in
different tenants, and — for a group that clears a volume gate — collects its logs, its
traces and the code at the deployed commit, and posts one threaded report to the owning
team's Slack channel. Read-only on Datadog, writes only to Slack (ADR-0023).

The input is what makes this cheap: an Error Tracking issue already names the exception
type, the message, **the file and the function it was raised in**, and the application
version it was first and last seen on. F1 has to infer where to look; F2 is told. That
`file_path` / `function_name` pair goes straight into `AnalysisRequest.paths`, which is the
field M7 3.3 showed was the difference between a `code_analysis` that read 47 files of build
config and one that reads the code.

Depends on: `analysis` and `ticket_pipeline` sub-graphs (M3/M1), the service map (M6), the
report renderer (M7 Phase 2), the analysis image (M7 Phase 3). It needs no Platform cron to
be developed — `make run-errors` ticks it by hand, as `make run-poller` does for F1.

## Decisions this plan needs recorded first

Two ADRs, written before Phase 2 (`/adr`). **Both written 2026-08-25**, against what the
capture showed rather than what the plan assumed:

- **ADR-0025 — code exceptions are polled hourly and gated by volume.** ADR-0018's gate is
  duration, which an error issue has no equivalent of. The gate here is occurrences: a floor
  per tick, plus a cumulative escalation so a slow bleed is not invisible forever. It must
  also record the departure from ADR-0017: F2 reads the environment from an `env:` filter in
  its own query, because APM and log events carry a usable `env` where Kubernetes monitor
  alerts do not. ADR-0017's rule is scoped to alerts and stays true there.
- **ADR-0026 — one exception across tenants is one finding.** The same
  `NullPointerException` in `plt-merck-qa` and `plt-hcl-software-uat` is one defect in one
  repository, and reporting it twice is reporting the tenancy, not the bug. Grouping is a
  rule over the repository the mono-tenancy rule resolves (`mapping/resolve.py`), never a
  model call.

Also flag, without deciding it here: F2 persists an error group and therefore *can* say
"this is the 4th time". ADR-0023's amended consequence says F1 cannot, and declined to build
a store of what was said. If F2's group table proves that store is cheap, F1's recurrence
should be revisited against it rather than against Jira.

## Public interface

- `config.yaml` gains an `errors:` block — `tracks`, `persona`, `min_occurrences`,
  `cumulative_occurrences`, `max_groups_per_tick`, `lookback_minutes`, `reanalyse_after`.
  `thresholds.ticket_confidence` gains `F2`; `Feature` gains `F2`.
- `triage.integrations.datadog.DatadogClient` gains `search_error_issues`,
  `get_error_issue` and `search_spans` (the exemplar trace; only `aggregate_spans` exists
  today), each behind its own concurrency gate, and their `FakeDatadogClient` replays.
- `triage.schemas.errors` — `ErrorIssue`, `ErrorGroup`, `ErrorCollection`.
- `triage.errors` — pure functions with no graph knowledge, as `triage.collect` is:
  `issues.py` (parse the envelope, the code-exception rule, the new-or-regressed rule),
  `grouping.py` (the group key and the cross-tenant collapse), `gate.py` (floor, cumulative
  escalation, per-tick cap), `sweep.py` (the three collectors and their reduction).
- `TriageRepository` gains `upsert_error_group`, `error_group`, `error_groups_open`;
  migration `0004_error_groups.py`.
- Graphs, registered in `langgraph.json`: `error_poller` (one node, the hourly tick) and
  `code_exception` (`open_group → collect_exception → qualify_exception → [analysis] →
  [ticket_pipeline] → settle_group`). `prompts/qualify_exception.md` produces the existing
  `Qualification`, so the Analysis sub-graph is untouched.
- `triage.report.render_code_exception(diagnosis, group, workload, collection) -> SlackReport`.
- `make run-errors`, `make capture-errors`, `deploy/platform/cron-error-poller.yaml` (hourly).

## Phase 1: the input exists, and says what we think it says

- [x] 1.1 One real hour of the org's Error Tracking issues is captured under
      `tests/fixtures/datadog/errors/<slug>/` by `make capture-errors`, and the capture states how
      many issues came back per track and how many of them name a file and a function. Everything
      after this phase is written against that capture, not against the OpenAPI spec.
- [x] 1.2 A tick asks for the `trace` and `logs` tracks with the `BACKEND` persona over one hour, in
      one call per track, and gets back each issue's counts and its attributes together.
- [x] 1.3 The environments Triage watches are a filter in the query — an issue from an environment no
      team configured is never returned, rather than returned and dropped.
- [x] 1.4 An issue that names an exception type *and* a source location is a code exception; one that
      names neither is recorded as skipped, with that as the reason, and never analysed.
- [x] 1.5 An issue whose `first_seen` falls in the tick window is new, and one whose regression
      reopened it in the window is new too — and the two are told apart, because a fix that did not
      hold is a different report from a defect nobody has seen before.
- [x] 1.6 An issue first seen before the window and not regressed produces nothing.
- [x] 1.7 A tick reads from its watermark minus an overlap; a poller that was down longer than the
      catch-up limit replays only the limit and says in the platform channel what it skipped.

## What Phase 1 measured, and what it changes

Done 2026-08-25 against the real org; the capture is
`tests/fixtures/datadog/errors/org_20260825_1h/`, and its `NOTES.md` is the record. Four
findings change what comes next.

- **Error Tracking is populated and names the code.** 15 issues in the reference hour, 202
  over seven days, and *every one of them* names both a file and a function. The plan's
  first open risk is closed and F2 has the input it was built on. The paths are
  fully-qualified Scala class names (`zeenea.repository.orientdb.OdbClient.scala`,
  `$anonfun$load$6`), not repository-relative paths, so 4.1 must map one to the other before
  `AnalysisRequest.paths` gets them.
- **The `logs` track is empty at every window and persona.** The org's issues come from APM
  spans alone. Nothing downstream should be surprised by an empty track.
- **`first_seen_version` is almost always blank** — 0 of 15 in the hour, 16 of 202 over a
  week, 47 of 320 over a month. Behaviours 4.2 and 4.3 are built on that field, so they are
  the minority path, not the normal one.
- **Phase 3 does not work as written, and this is measured, not suspected.** A query rebuilt
  from an issue's own fields (`service:X @error.type:"Y"`) returns **zero spans and zero
  logs** against an issue claiming 6,344 occurrences in the same hour. `service:X` alone
  returns 211,158 spans, so the service is instrumented; `status:error` and `@error.type:*`
  both return nothing, because the error spans are not retained and the aggregate answers
  `traffic_type: sampled`. Logs are barely shipped: 11 events for that service in the hour.
  **Phase 3 should be re-planned before it is built** — either it states the absence the way
  `not_instrumented` already does, or it finds another source. The issue's own sample event,
  if Error Tracking exposes one, is the obvious unprobed candidate.

Two numbers Phase 2 wants: 202 issues across 99 services collapse to **35** distinct (type,
file, function) triples, 5.8 to one — ADR-0026's case, larger than guessed. And occurrences
per issue in one hour ran 6344, 5869, 4009, 850, 835, 650, 435, 200, 29, 15, 4, 2, 2, 2, 1,
which is where `min_occurrences: 10` comes from.

## Phase 2: one exception, however many tenants

- [x] 2.1 Two issues with the same exception type, message shape and source location, seen in
      different `plt-*` services, are one group — because the mono-tenancy rule resolves both to the
      same repository — and the group names every service it was seen in and the count in each.
- [x] 2.2 Two issues that look alike but resolve to different repositories stay two groups, and a
      service that resolves to no repository is its own group, reported and never analysed.
- [x] 2.3 A group whose occurrences this tick are below the floor is persisted with its count and
      analysed nothing; the tick reports how many it held back.
- [x] 2.4 A group that stays below the floor tick after tick is analysed once its cumulative count
      crosses the escalation threshold — the slow bleed, made visible.
- [x] 2.5 A group already analysed is not analysed again until it regresses or crosses the next
      escalation interval, and the second report says which occurrence it is and links the first.
- [x] 2.6 A tick analyses at most `max_groups_per_tick` groups, ordered by occurrences, and names the
      groups it deferred rather than dropping them silently.

## What Phase 2 measured, and what it changes

Done 2026-08-25. Three findings.

- **The group key is the ADR's, not the behaviour's.** Behaviour 2.1 says "same exception
  type, message shape and source location"; [ADR-0026](../adr/0026-one-exception-across-tenants-is-one-finding.md)
  says type, source location and repository, with the message named as the finer key to reach
  for only if a group is ever shown to have merged two defects. The ADR is right and it is
  measured: the captured hour's six-tenant `EntityNotFoundException` group carries six
  different queried entities — `load_contact_by_id`, `load_inventory_item_by_path`,
  `load_user_by_email_read` — inside one message shape. Keying on the raw message collapses
  nothing at all (15 issues to 15 groups); keying on a normalised shape gives 12. The ADR's
  key gives **7**, which is the 6-tenant group the ADR argues from. The message is out.
- **The reference hour's own collapse is 2.1 to one, not 5.8.** 15 issues to 7 groups. The
  5.8 in Phase 1's notes is over seven days, where 202 issues span 99 services; one hour
  spans twelve. The hour reproduces the ADR's headline case exactly — `OdbClient.scala:
  $anonfun$load$6` in six tenants, one group, 10,763 occurrences — which is the check that
  can be made against a fixture at all.
- **The cumulative escalation needs the cooldown, or it is an error stream.** ADR-0025 sets
  the escalation at 100 cumulative occurrences. The loudest group of the reference hour does
  **10,763 an hour**, so it crosses the next escalation interval on every single tick, for
  ever. A re-analysis rule that only counted would repost the same defect hourly — precisely
  the failure ADR-0023 says to watch for. So `errors.reanalyse_after` gates the escalation
  path for a group that has already been reported: the escalation says *whether* there is
  more to say, the cooldown says *when* it may be said, and a **regression bypasses both**
  because a fix that did not hold is news the moment it happens. The first analysis of a
  group is untouched — a slow bleed still escalates at 100 with no wait.

## Phase 3: the log, the trace, and the window

- [x] 3.1 A gated group collects a bounded sample of the error logs behind it, reduced to templates
      and counts, and the sample keeps at least one complete stack trace — the stack is the whole
      point, and the template reduction must not eat it.
- [x] 3.2 It collects the error spans behind it, so the report can name the operation and one trace
      to open. A service with no APM instrumentation is `not_instrumented`, not empty — the
      distinction the collection schema already draws. **Amended:** a third outcome,
      `sampled_away`, for evidence Datadog counted and discarded (ADR-0027).
- [x] 3.3 The occurrences are re-found by a query built from the group's own fields, and the
      collection states that query verbatim, because it is a reconstruction and not the issue's own
      identity (see Open risks).
- [x] 3.4 The collection window runs back from the tick to the configured lookback, and the whole
      payload fits `collection.max_prompt_bytes`, stating every cut.
- [x] 3.5 A collector Datadog refuses is a stated failure and the run continues, exactly as F1's does.

## What Phase 3 measured, and what it changes

Done 2026-08-25. The phase opened with an investigation rather than code, because Phase 1
had measured its premise broken. Everything below was probed by hand against the live org,
read-only, over the reference window `2026-08-25T04:35Z`–`05:35Z`. The decision it produced
is [ADR-0027](../adr/0027-an-absence-datadog-discards-is-a-finding.md).

**There is no route to the occurrence-level evidence, and that is now measured rather than
assumed.** Four candidate routes, all dead:

1. *The issue's own sample event.* Nine endpoint shapes probed — `…/issues/{id}/events`,
   `/latest-event`, `/sample-event`, `/sample`, `/occurrences` — all **404**. Four `include`
   values — `sample_event`, `latest_event`, `event`, `issue,sample_event` — all **400
   `invalid include`**. Datadog's own v2 spec allows exactly `issue`, `issue.assignee`,
   `issue.case`, `issue.team_owners`, and `IssueAttributes` carries no stack, no `trace_id`
   and no `span_id`. The plan's "obvious unprobed candidate" does not exist.
2. *The stack already in the captured attributes.* Re-read in full: the detail payload is
   `error_message`, `error_type`, `file_path`, `first_seen`, `first_seen_version`,
   `function_name`, `is_crash`, `languages`, `last_seen`, `last_seen_version`, `platform`,
   `service`, `state`, plus `relationships.case`. `file_path` + `function_name` is the top
   frame and nothing more.
3. *A different query shape.* `service:plt-systeme-u-rec` returns **211,179** spans in the
   hour and **3,576,641** over seven days. `status:error` returns **0** in the hour and
   **48** over seven days — and those 48 are OTel `503`s on `POST /agent/register`, carrying
   no `@error.type`. `@error.type:*` returns **0** over both windows; the exact type returns
   **0**; `@error.message:*load_contact_by_id*` returns **0**. `@error.stack:*` returns
   **zero spans across the whole org**. Four issue-id join facets
   (`@error.tracking.issue.id`, `@issue.id`, `@error.fingerprint`, `issue_id`) return zero
   over seven days, and Datadog documents none of them — the only join key is
   `error.fingerprint`, which the *application* sets and Datadog never echoes back.
4. *A pre-sampling metric route.* `sum:trace.services_by_operation.hits{service:plt-systeme-u-rec}`
   = **921,775** in the hour; every `.errors` metric for that service = **0**. The exceptions
   are not flagged as span errors even in the metric stream, because the platform runs the
   **OpenTelemetry** Java agent (`io.opentelemetry.pekko-http-1.0`), where Datadog's
   100%-of-traffic guarantee for trace metrics does not hold.

**The disjointness is total, and it is the finding.** Three services *do* have retained error
spans in the reference hour — `gateway` (1,328), `scim-api` (836), `studio` (4) — and Error
Tracking has **no issues at all** for any of them, at either persona. The twelve services
that *do* have issues have no retained error spans. The two sets do not intersect.

**And the switch is one line of Datadog configuration, on Zeenea's side.**
`GET /api/v2/apm/config/retention-filters` says the org's **"Error Default" filter
(`spans-errors-sampling-processor`, `status:error`, rate 1) is `enabled: false`**. Datadog
documents the consequence exactly: *"Spans associated with the error need to be retained with
a custom retention filter in order for samples of that error to show up."* Enabling it is
what makes 3.1–3.3 return real evidence. Nothing in Triage can substitute for it.

**So Phase 3 is built honest rather than built differently.** The collectors exist, run, and
name which kind of nothing they found: `not_instrumented` when the control query is dead too,
and a new `sampled_away` when the control is alive and the issue counted occurrences — a team
can turn a retention filter on, and nobody turns one on for a report that only said "empty".
The reconstruction is layered (`@error.type` → `status:error` → the control) because the
layering is measured, and all three queries are stated verbatim beside the count the issue
claims. The stack-preserving rule is tested against
`tests/fixtures/datadog/errors/synthetic_stack/`, which is hand-written and labelled as such,
because no real one can be captured today.

**What an F2 report will contain in this org, today:** the exception type, its message, the
file and function, the count over the window, the tenants it was seen in, the Datadog issue
link — and a stated absence of logs and spans reading *"this query returned nothing, the
issue counts 5,869 occurrences in the same window, and the same services returned 211,179
spans with the error predicate dropped"*. Phase 4's report has to read well in that shape,
because that is the shape it will have.

One incidental fix: `Collector` now holds F2's three as well as F1's, so F1's follow-up loop
refuses them by name. Left to fall through they would have run as an unscoped event search —
an answer about the whole org, shaped like an answer about the incident.

## Phase 4: where in the code, and the report

- [ ] 4.1 The `code_analysis` reads the file and the function the issue named, ahead of the selection
      profile's own globs — the fix for what M7 3.3 measured.
- [ ] 4.2 When a repository claims the version the exception was first seen on, that is the commit
      the analysis reads; when nothing claims it, the commit falls back and the report says which of
      the two it got (ADR-0019, ADR-0020 — an observed version and a fallback must not read alike).
- [ ] 4.3 A group whose exception first appears at a version later than the previous one produces a
      `deployment` hypothesis naming both versions; with no `diff_analysis` entrypoint it comes back
      as a stated failure and lands in the report as an unknown, never as silence (ADR-0014).
- [ ] 4.4 The report carries the nine sections of `docs/ticket-spec.md` plus an exception header: the
      type, the message, the count over the window, the services it was seen in, the versions it was
      first and last seen on, and a link to the Datadog issue.
- [ ] 4.5 Every message about one group lands in one Slack thread, across ticks — the group row holds
      the thread, so the fourth occurrence replies under the first report rather than starting a
      fifth conversation.
- [ ] 4.6 A group settles as `reported` on its own row, and a run that dies leaves it recoverable
      rather than stuck mid-analysis, as `run_incident` does for a signal.

## Out of scope

- **RUM and browser errors.** No source maps, no cartography for the front-end repositories;
  most RUM issues would resolve to no repository and become noise.
- **Writing to Datadog.** The API can set an issue's state and assignee. Triage is read-only
  on production systems, and Error Tracking is one.
- **Jira.** Same as ADR-0023: the path stays configured off.
- **`diff_analysis`.** Still has no entrypoint (M7 3.4). Behaviour 4.3 states its absence
  rather than building it.
- **Retro-analysing the backlog of open issues.** The first tick looks back one hour like
  every other tick. A one-off sweep of everything already open is a separate decision, and
  a large one — the capture in 1.1 is what says how large.
- **F1's recurrence.** Flagged above, deliberately not folded in.

## Open risks

- ~~**Nothing here has ever been read from the real org, and Error Tracking may not be
  populated at all.**~~ **Closed by 1.1.** It is populated, and every issue names its file
  and function.
- ~~**An occurrence cannot be traced back to its issue by identity.**~~ **Confirmed, and
  worse than feared, and now closed as a decision rather than a risk.** The reconstruction
  returns nothing at all, not merely the wrong things; no API route exists to the exemplar
  either. See "What Phase 3 measured" and
  [ADR-0027](../adr/0027-an-absence-datadog-discards-is-a-finding.md): F2 states the absence
  and names the retention filter that would end it.
- **The volume floor is a guess until there are numbers.** F1's 15 minutes came from 961
  measured cycles. The distribution is now measured (see above) but no *outcome* is: whether
  ten occurrences an hour is worth a developer's attention is the first week's question. A
  floor set too low turns the team's channel into an error stream, which is the failure mode
  ADR-0023 says to watch for.
- **Grouping across tenants can hide a tenant-specific defect.** An exception that only ever
  happens for one customer is a fact about that customer's data or configuration, and a group
  that reports "seen in 6 services" flattens it. The per-service counts in 2.1 are the
  mitigation; whether they are enough is unknown until a real group exists.
- **`strict` still does not reach production** until LiteLLM is past v1.98.0 (ADR-0022), so
  `qualify_exception` will fail about half of its calls and lean on the retry budget, exactly
  as `qualify` does.
