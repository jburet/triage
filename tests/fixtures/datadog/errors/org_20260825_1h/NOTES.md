# What this capture settled (M8 behaviour 1.1)

`summary.md` is what the script measured. This is what it means, hand-written on
2026-08-25 and not regenerated. Treat these files the way
`tests/fixtures/datadog/hcl_software_uat_20260822/` is treated: Datadog retains
spans about fifteen days, so the window this describes cannot be re-captured, and
every number below was measured, not assumed.

## Error Tracking is populated, and it says what F2 hoped

The plan's first open risk — "Error Tracking may not be populated at all; if the
Scala platform's exceptions carry no `error.stack`, the hourly pass returns
nothing forever" — is closed. It does not happen.

- **15 issues in one hour**, `env:prod`, `BACKEND` persona, `trace` track.
- **15 of 15 name both a file and a function.** Widened to seven days: 202 of
  202. Thirty days: 320 of 320. This is the input F2 is built on, and it is
  there for every issue, not most of them.
- The file paths are Scala source resolved to the class
  (`zeenea.repository.orientdb.OdbClient.scala`, `$anonfun$load$6`), which is a
  fully-qualified class name and a synthetic lambda, not a repository-relative
  path. Behaviour 4.1 will have to map one to the other before it can hand
  `AnalysisRequest.paths` something a clone contains.

## The `logs` track is empty, and not by accident

Zero issues on the `logs` track over one hour, twenty-four hours and seven days,
at both `BACKEND` and `ALL` personas. The org's Error Tracking is fed by APM
spans only. Asking for both tracks costs one 11-byte call an hour, which is
cheap enough to keep as evidence that nothing changed; `config.yaml` ships
`tracks: [trace, logs]` for that reason and nothing downstream should be
surprised by an empty track.

## `env:` in the query works — the ADR-0017 departure is real

`env:prod` returns all 15; `env:preprod` returns 0; `-env:prod` returns 0. The
environment filter is applied by Datadog inside the search, so behaviour 1.3 is
a filter in the query and not a post-hoc drop. ADR-0017's rule — never read the
environment from an `env:` tag — was measured on *monitor alerts*, which carry
no usable one. APM events do. ADR-0025 records the departure.

## Version is mostly absent, which costs Phase 4

`first_seen_version` and `last_seen_version` are empty strings on **all 15**
issues in this hour. Widened: 16 of 202 over seven days, 47 of 320 over thirty.
The plan says an issue "names the application version it was first and last seen
on" — it names the field, and the field is empty for roughly 90% of issues,
because the services are not tagged with `version:`.

Behaviours 4.2 and 4.3 are built on that field. They are not impossible, but
they are the minority path: most groups will have no version, and the report has
to say so rather than fall back silently (ADR-0019, ADR-0020 already require
that shape).

## `get_error_issue` adds nothing a tick needs

The detail endpoint returns exactly the attribute set the search's `included`
already returned, plus the same `relationships.case`. Diffed field by field on
`395eb060`: identical. So one call per track is genuinely one call, and the
tick must not read issues individually. `search_error_issues` is the whole
input; `get_error_issue` stays for re-reading one issue by hand.

## The reconstructed query finds nothing — Phase 3's risk, measured

The plan's second open risk asked whether a query rebuilt from an issue's own
fields can find its occurrences, and warned about over- and under-matching. The
answer is worse and simpler:

| issue claims | `service:X @error.type:"Y"` over spans | over logs |
|---|---|---|
| 6344 | nothing | nothing |
| 5869 | nothing | nothing |
| 4009 | nothing | nothing |

`service:plt-systeme-u-rec` alone returns 211,158 spans over the same hour, so
the service is instrumented and indexed; `service:plt-systeme-u-rec status:error`
and `service:plt-systeme-u-rec @error.type:*` both return **zero**. The error
spans Error Tracking counted are not in the searchable span store — the aggregate
answers `traffic_type: sampled`, and the retention filters do not keep them.
Logs are barely shipped at all: 11 log events for that service in the hour,
against 5,869 counted exceptions.

Phase 3 as written — "collect the error logs behind it" and "collect the error
spans behind it" — collects nothing for these services. That is not a bug to fix
in the query; it is a fact about what this org retains. Phase 3 should be
re-planned before it is built: either it states the absence the way
`not_instrumented` already does, or it finds another source (the issue's own
sample event, if Error Tracking exposes one, is the obvious candidate and was
not probed).

## Numbers for the volume gate and the grouping rule

Measured for ADR-0025 and ADR-0026:

- Occurrences per issue in one hour: `6344, 5869, 4009, 850, 835, 650, 435, 200,
  29, 15, 4, 2, 2, 2, 1`. Five of fifteen below ten.
- New issues: 61 in seven days, 127 in thirty — about nine a day, so a typical
  hourly tick sees between none and one. **No issue in this capture's hour was
  new or regressed in it**, so the tick this fixture replays correctly produces
  nothing, which is behaviour 1.6 and is the common case.
- Regressions: 12 in seven days, 41 in thirty.
- Grouping: 202 issues over seven days across **99 distinct services** collapse
  to **35 distinct (type, file, function) triples** — 5.8 to one. Over thirty
  days, 320 issues to 69 triples. `EntityNotFoundException` at
  `OdbClient.scala:$anonfun$load$6` alone appears in six tenants inside this one
  hour. That ratio is ADR-0026's entire case, and it is larger than the plan
  guessed.

## Rate limits

Error Tracking published no `X-RateLimit-*` header on any of its seven calls.
Unknown is not generous, so the client serialises the family. The span and log
endpoints published theirs as before, and the log aggregate throttled once
during the reconstruction check (2 per 10 s, as measured on 2026-08-23).
