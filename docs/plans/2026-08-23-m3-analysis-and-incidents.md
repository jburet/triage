# Plan: M3 — Analysis sub-graph, F1 incident graph, Datadog ingress (2026-08-23)

> **Stale as of 2026-08-23.** Phase 2 and Phase 4 below predate
> [ADR-0016](../adr/0016-datadog-collected-by-triage.md),
> [ADR-0017](../adr/0017-alert-ingestion-by-polling.md) and
> [ADR-0018](../adr/0018-alert-persistence-gate.md): there is no Bits AI input, no Datadog
> webhook endpoint, and no analysis before the persistence gate. Phases 1 and 3 stand.
> Rewrite before executing.

Architecture §2.1, §2.3, §8; ADR-0005, ADR-0010, ADR-0011, ADR-0016, ADR-0017, ADR-0018. Depends on M2
Phases 1 and 3 (`AnalysisRunner`, `system_map_for_service`).

## Public interface

- `triage.graphs.analysis`: graph `analysis`. Input: `hypotheses: list[Hypothesis]`, `signal`,
  `system_map` context. Output: `diagnosis: Diagnosis`.
- `triage.graphs.incident`: graph `incident`, registered in `langgraph.json`. Input: a `Signal`
  with `feature = F1`. Composes `analysis` then `ticket_pipeline`, then drafts a post-mortem.
- `triage.integrations.datadog.DatadogClient` (protocol) — `bits_ai_investigation(alert_id)`,
  `query_metrics(...)`, `search_logs(...)`, `search_traces(...)`; `FakeDatadogClient` with
  canned fixtures; real client over the Datadog MCP server.
- `triage.integrations.kubernetes.KubernetesReader` (protocol) — `events_for(service, window)`,
  `pods_for(service)`; read-only; fake + real.
- `Deps` gains `datadog` and `k8s`.
- `triage.ingress`: FastAPI app with `POST /webhooks/datadog`. Validates the signature, persists a
  `Signal`, creates a run on the Platform (or invokes in-process under the ADR-0011 fallback),
  returns 202. No business logic.
- Prompts: `classify_alert.md`, `qualify.md`, `diagnose.md`, `postmortem.md`.
- Fixtures: `tests/fixtures/alerts/*.json` (Datadog payloads), `tests/fixtures/bits_ai/*.json`,
  `tests/fixtures/analysis_results/*.json`.

## Phase 1: Analysis sub-graph (ADR-0005)

- [ ] 1.1 Given one `app` hypothesis and a fake runner, `analysis` produces a `Diagnosis` whose `location` names the repo and commit from the hypothesis and whose `evidence` includes the code analysis finding.
- [ ] 1.2 Each cause type is routed to its branch: `app` → `code_analysis`, `infra` → `iac_analysis`, `deployment` → `diff_analysis` with both commits, `dependency` → no runner call at all.
- [ ] 1.3 Of N hypotheses, only those with `rank_score ≥ analysis.min_rank_score` are analysed, capped at `analysis.max_hypotheses`, and at least one is always analysed even if none clears the floor.
- [ ] 1.4 Hypotheses considered but not analysed appear in the diagnosis `ruled_out` with the qualifier's reason; analysed hypotheses the `diagnosis` tier rejects appear there with the analysis finding as `why`.
- [ ] 1.5 A failed analysis for one hypothesis does not fail the run: the diagnosis records an `unknowns` entry naming the hypothesis and the failure, and confidence for a cause that relied on it is capped at `medium`.
- [ ] 1.6 The synthesised `Diagnosis` always passes its own validator (`_confidence_is_earned`); a synthesis that would not is retried once with the validation error fed back, then degraded to `low`.

## Phase 2: F1 collection and qualification

- [ ] 2.1 A Datadog alert fixture is classified by the `triage` tier into an alert type, a collection window and a collector list; the window is bounded by the alert's own timestamps.
- [ ] 2.2 When Bits AI returns an investigation, it is the primary input to `qualify` and its findings appear as `evidence` with URLs.
- [ ] 2.3 When Bits AI is unavailable, the graph retries once after 60 s, then proceeds without it, adds an `unknowns` entry saying so, and the final diagnosis confidence is at most `medium` (ADR-0004).
- [ ] 2.4 `collect_gaps` only calls collectors the classification named and Bits AI did not cover; Kubernetes events for the service over the window are attached as `k8s_event` evidence.
- [ ] 2.5 `qualify` produces a ranked `Hypothesis` list where every `app`/`deployment` hypothesis carries the deployed commit resolved from the system map, and an unresolvable commit yields a `dependency` or `infra` hypothesis instead of an invented commit.

## Phase 3: F1 end to end and post-mortem

- [ ] 3.1 The `incident` graph, fed an alert fixture with all fakes, ends in a Jira ticket (the `oom_payments` path) and the Slack channel of the owning team has both the immediate notice and the ticket notice.
- [ ] 3.2 An immediate Slack notice is posted before any analysis starts, naming the service, alert and that Triage is investigating; it is the thread the later notices reply to.
- [ ] 3.3 After a ticket is created, a post-mortem draft (timeline + diagnosis) is added as a Jira comment on that ticket and the Slack thread receives a link, not the text (ADR-0010).
- [ ] 3.4 No post-mortem is drafted when the pipeline ends without a ticket.
- [ ] 3.5 `Signal.status` moves `received → analysing → diagnosed → ticketed|discarded`, and `failed` on an unhandled error, observable through the repository.

## Phase 4: ingress (ADR-0011)

- [ ] 4.1 `POST /webhooks/datadog` with a valid signature persists a `Signal` and returns 202 with the signal id; an invalid signature returns 401 and persists nothing.
- [ ] 4.2 A replayed webhook (same `external_id`) returns 202 and does not create a second signal or run.
- [ ] 4.3 With `TRIAGE_PLATFORM_URL` set, a run is created on the Platform for the `incident` graph; without it, the graph is invoked in-process with the Postgres checkpointer. Same graph, same thread id either way.
- [ ] 4.4 A Datadog alert for a service not in the system map is accepted, routed to the platform team's channel as a Slack notice, and discarded — never analysed.

## Out of scope

- Real Datadog MCP and Kubernetes clients beyond the protocol and a smoke test — they need cluster credentials and are verified during the infra track.
- GitHub merge webhook (`POST /webhooks/github`) — small, but belongs with M2's incremental refresh; add it to the ingress when M2 Phase 4 is green.
- Jira closure-feedback webhook — M5.
- Change correlation beyond deployments (feature flags, vendor incidents) — roadmap cross-cutting, unplanned.

## Open risks

- Bits AI output shape is unknown until it is enabled (roadmap "Before starting"). Phase 2 fixtures are assumptions; replace them with real captures before calling 2.2 done.
- The Datadog MCP server's tool surface may not expose Bits AI at all. If so, `bits_ai_investigation` becomes a REST call and the protocol stays the same.
