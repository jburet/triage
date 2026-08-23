# Plan: M3 — Analysis sub-graph, F1 incident graph, Datadog collection (2026-08-23)

Architecture §2.1, §2.3, §8; ADR-0005, ADR-0010, ADR-0011, ADR-0016, ADR-0017, ADR-0018.
Depends on M2 Phases 1 and 3 (`AnalysisRunner`, `system_map_for_service`).

Phases 2 and 4 were rewritten on 2026-08-23 after collecting one real alert by hand
(`tests/fixtures/datadog/hcl_software_uat_20260822/`). The numbers in the caps below come
from that capture, not from estimation.

## Public interface

- `triage.graphs.analysis`: graph `analysis`. Input: `hypotheses: list[Hypothesis]`, `signal`,
  `system_map` context. Output: `diagnosis: Diagnosis`.
- `triage.graphs.incident`: graph `incident`, registered in `langgraph.json`. Input: a `Signal`
  with `feature = F1`. Composes `analysis` then `ticket_pipeline`, then drafts a post-mortem.
- `triage.integrations.datadog.DatadogClient` (protocol) — `search_events(query, frm, to, limit)`,
  `get_monitor(id)`, `query_timeseries(query, frm, to)`, `aggregate_logs(...)`, `search_logs(...)`,
  `aggregate_spans(...)`. Real client over REST v1/v2 with `httpx`: host from
  `TRIAGE_DATADOG_SITE`, `DD-API-KEY` and `DD-APPLICATION-KEY` headers.
  `FakeDatadogClient` replays `tests/fixtures/datadog/` keyed by (collector, query).
- `triage.collect`: the alert-class recipes, the sweep, the reduction, the follow-up budget.
  Pure functions over collector results — no graph knowledge.
- `triage.nodes.poll_alerts`: one poller tick. Registered as a 60-second Platform cron in
  `langgraph.json`. There is no Datadog endpoint on the ingress.
- `Deps` gains `datadog`.
- Prompts: `classify_alert.md`, `qualify.md`, `diagnose.md`, `postmortem.md`, `follow_up.md`.
- `config.yaml` gains, per team, `service_patterns`, `namespace_patterns` and `environments`;
  a top-level `clusters:` map of cluster name → environment; and:

  ```yaml
  collection:
    window_multiplier: 4          # of the monitor's own evaluation window
    window_min_minutes: 15
    window_max_hours: 6
    max_followup_calls: 6
    max_log_templates: 15
    max_log_lines: 25
    max_events: 40
    max_timeseries_series: 6
    max_timeseries_points: 60
    max_prompt_bytes: 60000
  thresholds:
    alert_persistence_minutes: 15
    flap_count: 5
    flap_window_hours: 24
  ```

- `signals` gains: Datadog event id (as `external_id`), monitor id, firing group, cycle
  duration, recovery time; statuses `out_of_scope` and `self_recovered`. One row holds the
  poller watermark.
- Fixtures: `tests/fixtures/datadog/hcl_software_uat_20260822/` (captured from the live org),
  and `tests/fixtures/alert_events/*.json` (poller pages, hand-built for the routing cases).

## Phase 1: Analysis sub-graph (ADR-0005)

- [x] 1.1 Given one `app` hypothesis and a fake runner, `analysis` produces a `Diagnosis` whose `location` names the repo and commit from the hypothesis and whose `evidence` includes the code analysis finding.
- [x] 1.2 Each cause type is routed to its branch: `app` → `code_analysis`, `infra` → `iac_analysis`, `deployment` → `diff_analysis` with both commits, `dependency` → no runner call at all.
- [x] 1.3 Of N hypotheses, only those with `rank_score ≥ analysis.min_rank_score` are analysed, capped at `analysis.max_hypotheses`, and at least one is always analysed even if none clears the floor.
- [x] 1.4 Hypotheses considered but not analysed appear in the diagnosis `ruled_out` with the qualifier's reason; analysed hypotheses the `diagnosis` tier rejects appear there with the analysis finding as `why`.
- [x] 1.5 A failed analysis for one hypothesis does not fail the run: the diagnosis records an `unknowns` entry naming the hypothesis and the failure, and confidence for a cause that relied on it is capped at `medium`.
- [x] 1.6 The synthesised `Diagnosis` always passes its own validator (`_confidence_is_earned`); a synthesis that would not is retried once with the validation error fed back, then degraded to `low`.

## Phase 2: F1 collection and qualification (ADR-0016)

- [x] 2.1 `classify_alert` asks the `triage` tier for an alert class from a closed enum and nothing else. The window is a rule — the monitor's own evaluation window × `window_multiplier`, clamped to `[window_min_minutes, window_max_hours]` — and the collectors come from the class recipe, so a class the model cannot decide degrades to the `generic` recipe instead of failing the run.
- [x] 2.2 The monitor's query, thresholds, options, priority and firing groups are read from the alert event itself; `get_monitor` is only called when the event lacks them, and never in the sweep.
- [x] 2.3 The sweep re-runs the monitor's own query **in the idiom it was written in** — timeseries for a metric monitor, event search for an `event-v2 alert`, log search for a log monitor — and records that it could not when the type has no re-runnable form. The reference monitor is an event monitor whose query returns the three container kills and their exit codes; sent to the timeseries API instead it returns a 400, which reads as *no data*.
- [x] 2.4 The sweep runs its recipe's collectors concurrently within Datadog's per-endpoint limits — measured 2026-08-23: spans 5 per 60 s, logs search 3 per 10 s, logs aggregate 2 per 10 s — honouring `x-ratelimit-reset` on a 429. It returns a `Collection` in which every collector records whether it ran and whether it returned data; one collector raising does not fail the sweep, it is recorded as failed.
- [x] 2.5 Events are collected at **both** `service:` and `kube_namespace:` scope. Against the captured fixture, the liveness-probe failures and the container exit code — present only at namespace scope — appear as `k8s_event` evidence.
- [x] 2.6 A Kubernetes change event is diffed, not read by title: of the 18 namespace events captured, the two carrying `change_metadata` have `prev_value` and `new_value` differing only in `ready_replicas`, so the collection reports no spec change and raises no `deployment` hypothesis from them.
- [x] 2.7 Logs are deduplicated by message template before sampling. The captured window's 60 entries — 133 KB on the wire, 45 of them the same `platform api authentication failed`, 11 distinct templates by hand and 13 by the normaliser — reduce to at most `max_log_templates` templates with counts plus at most `max_log_lines` verbatim lines.
- [x] 2.8 The rendered collection handed to `qualify` stays under `collection.max_prompt_bytes`; when a collector's share would exceed it, that collector is truncated and the truncation is stated in the rendered text rather than being silent.
- [x] 2.9 A collector returning nothing is re-run namespace-wide over 7 days before it is recorded. Empty in both yields an `unknowns` entry naming the collector (the captured tenant has no APM in either); empty only in the incident window is passed to `qualify` as evidence, not as a gap.
- [x] 2.10 `follow_up` lets the `analysis` tier request up to `collection.max_followup_calls` further calls from the same collector set. A request beyond the budget is refused and recorded; a request naming a collector outside the set is discarded, with the discarded request preserved in state.
- [x] 2.11 `qualify` produces a ranked `Hypothesis` list where every `app`/`deployment` hypothesis carries the deployed commit resolved from the system map, and an unresolvable commit yields a `dependency` or `infra` hypothesis instead of an invented commit.

Scored in `evals/`, not here, because it depends on model output: fed the captured fixture,
`qualify` should rank an `infra` hypothesis naming the liveness probe first, and the
`StatefulSet … deployed` event should not produce a `deployment` hypothesis.

## Phase 3: F1 end to end and post-mortem

- [x] 3.1 The `incident` graph, fed an alert fixture with all fakes, ends in a Jira ticket (the `oom_payments` path) and the Slack channel of the owning team has both the immediate notice and the ticket notice.
- [x] 3.2 A Slack notice is posted when the persistence gate opens and analysis starts, naming the service, alert, how long it has been firing, and that Triage is investigating; it is the thread the later notices reply to.
- [x] 3.3 After a ticket is created, a post-mortem draft (timeline + diagnosis) is added as a Jira comment on that ticket and the Slack thread receives a link, not the text (ADR-0010).
- [x] 3.4 No post-mortem is drafted when the pipeline ends without a ticket.
- [x] 3.5 `Signal.status` moves `received → waiting → analysing → diagnosed → ticketed|discarded`, terminates at `out_of_scope` or `self_recovered` without ever reaching `analysing`, and reaches `failed` on an unhandled error — all observable through the repository.

## Phase 4: alert poller (ADR-0017, ADR-0018, ADR-0011)

- [x] 4.1 One tick against a fixture page of monitor-alert events persists one `Signal` per (monitor id, firing group) in `error`, with the Datadog event id as `external_id`, and advances the watermark. A second tick over the same page persists nothing new and creates no run.
- [x] 4.2 Each tick queries from `watermark − 2 min`; an event inside that overlap which was already stored is deduplicated on `external_id`, and a re-notification of an open cycle does not create a second signal.
- [x] 4.3 An alert whose service matches a team's `service_patterns` resolves to that team; one matching no team is persisted `out_of_scope` and posts nothing to Slack; one that resolves to a team but names a service unknown to the system map posts the notice to that team's channel and is never analysed.
- [x] 4.4 An alert with no `service:` tag resolves through `kube_namespace` / `kube_stateful_set` against `namespace_patterns`, which is how the captured StatefulSet alert reaches a team at all.
- [x] 4.5 Environment comes from `clusters:` mapping `kube_cluster_name`, never from an `env:` tag. A cluster mapping to an environment outside the team's `environments` is `out_of_scope`; an unmapped cluster is `out_of_scope` with that reason, never assumed to be production.
- [x] 4.6 A cycle that recovers before `thresholds.alert_persistence_minutes` is stored `self_recovered` with its duration and never analysed; a cycle still in `error` when the gate is reached creates a run for the `incident` graph.
- [x] 4.7 `thresholds.flap_count` self-recovered cycles for the same monitor and group inside `thresholds.flap_window_hours` produce one flapping `Diagnosis` through the ticket pipeline, after which the counter for that pair resets.
- [x] 4.8 After a gap longer than the catch-up bound the poller replays at most 30 minutes, posts one Slack line naming the skipped span and how many events it contained, and does not silently advance the watermark past them.
- [x] 4.9 With `TRIAGE_PLATFORM_URL` set a run is created on the Platform for the `incident` graph; without it the graph is invoked in-process with the Postgres checkpointer. Same graph, same thread id either way.

## Out of scope

- **`KubernetesReader`, dropped rather than deferred.** Every Kubernetes fact the captured
  incident needed — probe failures, kill reason, exit code, restart counts, replica counts,
  the full before/after StatefulSet spec — came from Datadog. Add a cluster credential when
  an incident proves it is needed, not before.
- Replying inside the Datadog message's own Slack thread. `SlackClient` takes `thread_ts`
  now (ADR-0017) so this stays a small change; it needs a bot with `channels:history`.
- GitHub merge webhook (`POST /webhooks/github`) — belongs with M2's incremental refresh.
- Jira closure-feedback webhook — M5.
- Alert coverage audit. The measurements in the roadmap argue it should come before F1; that
  is a roadmap decision, not this plan's.
- Change correlation beyond deployments (feature flags, vendor incidents).

## What the first live run changed (2026-08-23)

`make run-incident` against a real alert — `grafana-observability-metrics` in
`preprod-euw3`, 06:21 UTC — found three defects the captured fixture could not, because
its tenant name was globally unique and its monitor was an event monitor:

- **Metrics were not narrowed to the firing group.** `sum:…replicas_ready{kube_stateful_set:X}`
  summed every cluster running a StatefulSet of that name and answered *7 ready of 8
  desired* for a workload that was at *0 of 1*. Scoped to the group it answers 1 → 0.
- **The monitor's own query was re-run unscoped.** `{*} by {cluster,namespace,statefulset}`
  returns every group in the org; the reduction then kept whichever six series came first,
  all reading 100%. Scoped, it shows the group falling 100 → 0.
- **Emptiness was widened by scope instead of by time.** A `service:` tag that does not
  exist was reported as "the absence is about this incident" because the *namespace* around
  it was busy. The same query over seven days says what it should: not collected at all.

A second live run, on `Zeenea service or platform pod down in prod` for
`plt-hcl-software-uat`, found two more:

- **An alert with only a `service:` tag resolved to no environment**, so the monitor F1
  exists for would have had every alert dropped. The environment is now read from the
  monitor's own `env:` filter when there is no cluster ([ADR-0017](../adr/0017-alert-ingestion-by-polling.md),
  amended).
- **Metrics scoped only by cluster and namespace were dropped entirely** for that alert,
  losing the restart count — 4 → 10 in the window, the strongest evidence a crash-restart
  diagnosis has — and the memory curve. Both are tagged `service:` in this org, so a
  spec now falls back to it.

## Open risks

- **The flap thresholds do not fit this monitor.** Measured 2026-08-23 over seven days:
  56 transitions, 9 tenant groups, **every cycle between 2 and 9 minutes** — so nothing
  passes the 15-minute gate, exactly as ADR-0018 predicted. But per group the rate is about
  one cycle a day, so `flap_count: 5` over `flap_window_hours: 24` never fires either, and
  Triage produces *nothing at all* for the monitor it was built for. Across groups the same
  monitor shows ~28 cycles in seven days, which is plainly a fleet-wide pattern. Either the
  flap window becomes days rather than hours, or flapping is counted per monitor as well as
  per (monitor, group). That is a decision for ADR-0018, not a tuning change.

- **One incident, one class.** The recipes for latency, error-rate and saturation classes are
  written from the shape of the crash/restart one. Capture a real alert per class before
  calling 2.1 done; the exploration script that produced the existing capture is the tool for it.
- **Fixtures age out of the source.** Datadog retains logs and spans around 15 days, so the
  captured JSON is the only permanent record of these responses — it cannot be regenerated
  from the same incident after that.
- **The tenant pattern is an assumption.** `plt-<customer>[-<env>]` held across every group
  observed, but it is a naming convention, not a contract; 4.3 fails loudly (out of scope
  with a reason) rather than guessing when it does not match.
- **Rate limits are tight where it hurts and undocumented.** Measured from response headers
  on 2026-08-23: `spans_public_api` **5 per 60 s**, `logs_public_search_api` 3 per 10 s,
  `logs_public_analytics_aggregate` 2 per 10 s; events search and monitor reads are
  effectively unlimited (12,000/60 s and 3,000/10 s). A single sweep already spends two of
  the five span calls, so concurrent incidents will throttle each other and the follow-up
  loop can exhaust the budget alone. The client must serialise span and log calls across
  concurrent runs, not merely retry — and the limits are org-scoped and can be raised on
  request, which may be the cheaper fix.
