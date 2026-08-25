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

- [ ] 2.1 Two issues with the same exception type, message shape and source location, seen in
      different `plt-*` services, are one group — because the mono-tenancy rule resolves both to the
      same repository — and the group names every service it was seen in and the count in each.
- [ ] 2.2 Two issues that look alike but resolve to different repositories stay two groups, and a
      service that resolves to no repository is its own group, reported and never analysed.
- [ ] 2.3 A group whose occurrences this tick are below the floor is persisted with its count and
      analysed nothing; the tick reports how many it held back.
- [ ] 2.4 A group that stays below the floor tick after tick is analysed once its cumulative count
      crosses the escalation threshold — the slow bleed, made visible.
- [ ] 2.5 A group already analysed is not analysed again until it regresses or crosses the next
      escalation interval, and the second report says which occurrence it is and links the first.
- [ ] 2.6 A tick analyses at most `max_groups_per_tick` groups, ordered by occurrences, and names the
      groups it deferred rather than dropping them silently.

## Phase 3: the log, the trace, and the window

- [ ] 3.1 A gated group collects a bounded sample of the error logs behind it, reduced to templates
      and counts, and the sample keeps at least one complete stack trace — the stack is the whole
      point, and the template reduction must not eat it.
- [ ] 3.2 It collects the error spans behind it, so the report can name the operation and one trace
      to open. A service with no APM instrumentation is `not_instrumented`, not empty — the
      distinction the collection schema already draws.
- [ ] 3.3 The occurrences are re-found by a query built from the group's own fields, and the
      collection states that query verbatim, because it is a reconstruction and not the issue's own
      identity (see Open risks).
- [ ] 3.4 The collection window runs back from the tick to the configured lookback, and the whole
      payload fits `collection.max_prompt_bytes`, stating every cut.
- [ ] 3.5 A collector Datadog refuses is a stated failure and the run continues, exactly as F1's does.

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
  worse than feared.** The reconstruction returns nothing at all, not merely the wrong
  things. See "What Phase 1 measured" above; Phase 3 needs replanning.
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
