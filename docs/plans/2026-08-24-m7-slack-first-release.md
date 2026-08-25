# Plan: M7 — the first Slack release (2026-08-24)

[ADR-0023](../adr/0023-the-first-release-writes-only-to-slack.md) (Slack only, Jira and F3
postponed), ADR-0002 (thresholds now frame rather than route), ADR-0014 (the analysis
entrypoint), ADR-0017/0018 (polling and the persistence gate). Supersedes M4 as the next
milestone; M5 waits on there being reports to evaluate.

**The release**: a Datadog alert that has persisted for its gate becomes one threaded report
in the owning team's Slack channel, containing what happened, what explains it, what has been
ruled out and what is not known — with the code and the infrastructure code read at the
deployed commit. Nothing is written anywhere else.

Everything in Phases 1–2 runs today's code and is verifiable offline. Phases 3–5 are the
infra track, which this release is the first thing to actually need.

## Public interface

- `config.yaml` gains `writes:` — `slack` (the release) or `slack_and_jira`. Default `slack`.
- `triage.report`: `render_incident(diagnosis, workload, collection) -> SlackReport`, one
  renderer with two framings chosen by confidence against `thresholds.ticket_confidence`.
- `docker/analysis/Dockerfile` — the image `triage.analysis.entrypoint` runs in.
- `deploy/` — the Job template, gVisor `RuntimeClass`, `NetworkPolicy`, the analysis role,
  and the Platform's cron object.
- `scripts/run_poller.py` stays the manual entry point; the cron replaces the schedule, not
  the code.

## Phase 1: nothing reaches Jira, and the gate stops routing

- [x] 1.1 With `writes: slack`, a diagnosis above the confidence threshold produces a Slack report and no Jira call is made — asserted against the recording fake, which must record nothing.
- [x] 1.2 With `writes: slack_and_jira`, today's behaviour is unchanged, so the decision is reversible by configuration and the Jira path stays covered.
- [x] 1.3 The confidence threshold selects the report's framing rather than its destination: at or above it the report leads with the probable cause, below it with what is established and what is missing.
- [x] 1.4 A run that would previously have ended at `notify_review_exhausted` now ends as a report carrying the same draft, because there is no filing decision left to exhaust.
- [x] 1.5 `docs/ticket-spec.md` is restated as the report spec, with the nine sections intact and the Jira workflow section marked postponed.

## Phase 2: the report is the product

- [x] 2.1 The report renders every section the diagnosis carries — symptom with numbers and window, impact, probable cause with confidence, evidence with links, location as repository/commit/paths, expected change, out of scope, ruled out, unknowns — and omits nothing silently: a section with no content says why.
- [x] 2.2 The location names the repository, the deployed commit and the IaC path, and carries the mapping rung: an image-derived commit and a `serves`-pattern one must not read alike (ADR-0019, ADR-0020).
- [x] 2.3 Every message about one incident lands in the thread opened by `open_incident`. Not the recurrence escalations (ADR-0003): they never fire in this release, so a checkbox naming them would be claiming a verification nothing can perform — see ADR-0023's amended consequence.
- [x] 2.4 A report longer than Slack accepts is split at a section boundary, never mid-evidence, and the split is stated.
- [x] 2.5 The two causes ruled out at the 0.30 analysis floor and the six unknowns produced by the 2026-08-24 19:05 run all appear in the rendered report — the fixture is that run, so the thing this milestone exists to deliver is what the test asserts. (The plan said four ruled out; the 19:05 run's own output shows four *ranked* causes, of which two were ruled out at the floor. Corrected to what the run printed.)

## Phase 3: the analysis image

- [x] 3.1 `docker/analysis/Dockerfile` builds an image that runs `triage.analysis.entrypoint` for a given `AnalysisRequest` and writes an `AnalysisResult`, with no Triage source on the path but the entrypoint and its schemas.
- [x] 3.2 The image clones over https at the requested commit and refuses a ref it was not given.
- [x] 3.3 Running the image locally against the 2026-08-24 hypotheses produces a `code_analysis` and an `iac_analysis` result — the first time either has ever run.
- [x] 3.4 `diff_analysis` still has no entrypoint and still fails as a stated failure naming the kind; the diagnosis records it as an unknown and caps confidence (ADR-0014). This release does not add it.
- [ ] 3.5 The image is published to the infra account's registry and named in `config.analysis.job`. **Blocked**: no credentials for the infra account (097607883991), no ECR repository exists for this image, and what was built is `linux/arm64` — the cluster needs `linux/amd64`, so publishing means a buildx cross-build as well as a login.

What the first two live analyses showed (2026-08-24, one `analysis` call each, through the
proxy). The `iac_analysis` on `platform-infra` at `68648d21` answered `high` and *eliminated*
the reference incident's leading cause with a mechanism: the platform StatefulSet declares a
`startup_probe` (`initial_delay_seconds=120, period_seconds=30, failure_threshold=120`), and
Kubernetes suppresses the liveness probe until it passes, so a liveness probe shorter than
startup cannot restart the pod. That is a real answer, from real code, that no one had.

The `code_analysis` on `datacatalog` at `fcb58d1b` answered `low`, and the reason is the
gather, not the model: `APPLICATION` selected 47 files out of 4261 and **not one line of
Scala** — twenty-six `build.sbt` files, the READMEs, ten GitHub workflows, two
`docker-compose.yml`, and the Helm chart. The model said so, in a finding whose paths are
`not_examined`. So the milestone's open risk "confidence may never exceed low even with
analyses running" is answered in two parts: an IaC repository the mapping points a path
into reaches `high`, and an application repository whose language the profile does not
name reaches `low` because nothing worth reading was opened. The fix belongs to
`triage.analysis.context`, not to the image.

## Phase 4: the sandbox

- [x] 4.1 The Job template, the `NetworkPolicy` (egress to GitHub and the registry only) and the read-only role the Job writes its result with exist as manifests under `deploy/`.
      Validated with `kubeconform -strict -kubernetes-version 1.31.0` (10 resources, 0 invalid) and
      by `tests/unit/test_deploy_manifests.py`, which holds the reviewed Job template to the object
      `job_manifest` submits. `kubectl --dry-run=client` cannot validate here: it fetches its schemas
      from an API server. Two corrections are in `deploy/README.md` — egress to the registry is
      nothing (image pulls are the kubelet's traffic), and the ADR-0009 "insert-only" role is
      SELECT/INSERT/UPDATE on one table because `save_analysis_result` reads the row back.
- [ ] 4.2 `KubernetesJobApi` submits one and reads its result against a real cluster — the first time it has spoken to one.
      *Blocked on a cluster.* Needs: a namespace on an EKS cluster with gVisor nodes, credentials
      that can apply `deploy/`, and a Postgres it can reach. Nothing in code is waiting on it.
- [ ] 4.3 A Job that exceeds its deadline or its memory limit is reported as a stated failure, not a hang: the analysis fails, the diagnosis records why, and the report says so.
      Two thirds done offline. The Job now *has* a memory, cpu and ephemeral-storage limit
      (`config.analysis.job.resources`), the runner waits a minute longer than the Job may live so
      Kubernetes' `DeadlineExceeded` is what gets reported rather than the client's own timeout, and
      the failure carries the Failed condition's reason, its message and the limits the Job was given
      — `tests/unit/test_kubernetes_job_runner.py`, and the diagnosis half in
      `tests/integration/test_analysis.py`. Unticked because "the report says so" waits on the Phase 2
      renderer, and no Job has ever been killed for real: the failure shapes come from the API
      reference, not from a cluster.
- [ ] 4.4 The Job's egress is verified to be refused everywhere it was not granted, by trying.
      *Blocked on a cluster.* `deploy/41-job-egress-probe.yaml` is the trying: same labels,
      RuntimeClass and security context as an analysis, curl once per destination, non-zero exit if
      any of them disagrees with what was granted. Never applied.

## Phase 5: it runs itself

**There is no Enterprise licence for an on-prem deployment (2026-08-25), so ADR-0011's
fallback is the design.** The graphs, nodes and schemas are unchanged — that is the property
ADR-0011 bought — but three things this phase assumed the Platform would provide now have to
be built, and 5.1 and 5.2 are rewritten to say what they are.

- [ ] 5.1 Triage runs its own graphs in one long-lived process against the shared Postgres, with its tables in the `triage` schema and `langgraph-checkpoint-postgres` wired so a restart resumes rather than loses an incident.
      The checkpointer is a declared dependency and is used **nowhere**: durability today is zero,
      and a process that dies mid-incident leaves a signal in `analysing` that nothing resumes and
      nothing reclaims. Checked offline so far: `tests/integration/test_registered_graphs.py`
      compiles all six entries in `langgraph.json`, and `tests/unit/test_migrations.py` holds every
      table to the `triage` schema.
- [ ] 5.1b The poller launches an incident as a supervised task with a concurrency cap, and keeps polling while it runs.
      New, and the real cost of losing the Platform: `_launch` *awaits* `run_incident` inside the
      tick, so a signal that opens the gate stops polling for the length of the analysis — 64s
      measured — and two signals in one tick run serially. ADR-0011 said the queue would be replaced
      by "the graph's own concurrency limits"; nothing limits concurrency, it simply runs to
      completion in the caller.
- [ ] 5.1c Triage has a runtime image.
      M7 built the *analysis* image; the poller has only ever run from a checkout. Whatever
      schedules it needs one, and the registry that blocks the analysis image blocks this too.
- [ ] 5.2 A Kubernetes schedule ticks the poller every 60 seconds, and a cycle that has not persisted for its gate creates no run (ADR-0018).
      `deploy/platform/cron-alert-poller.yaml`, `scripts/apply_cron.py` and the cron methods on
      `PlatformRestClient` are now **unreachable** — kept, not deleted, because if a licence ever
      arrives deployment changes and nothing else does (ADR-0011). What replaces them is a
      Kubernetes object over `scripts/run_poller.py`, which already takes `--every 60`. Deployment
      or CronJob is undecided: a CronJob is self-healing per tick but `concurrencyPolicy: Forbid`
      would *skip* ticks while an in-process incident runs, and a Deployment keeps a warm process
      but needs a liveness probe on watermark progress to catch a wedge. 5.1b decides it, because a
      poller that does not block changes the answer. The second half is verified live: one tick against the real Datadog org over a
      25-minute window read 20 monitor-alert events — 4 cycles opened, 12 out of scope, 2
      self-recovered with their durations, 1 in scope with no cartography and told about, **0
      launched**. The gate, holding, against real alerts.
- [x] 5.3 Two alerts for the same monitor and firing group inside one cycle produce one run, not two.
      It did not, on the Platform path: `create_run` returns as soon as the run is queued, so the
      signal stayed `waiting` and the next tick launched it again — one run a minute for as long as
      the alert fired. The poller now claims the signal before launching and puts it back if the
      launch raises (`tests/integration/test_poller.py`, re-notified with a fresh event id).
- [ ] 5.4 A real production alert reaches a real Slack channel, analysed, without anyone running a command.
      *Blocked on 5.1 and on Phases 1–3.* Needs the Platform running the cron, the analysis image
      published, and a real Slack token — `TRIAGE_DRY_RUN=0` with a bot in the team's channel.

## Out of scope

- **Jira.** The path stays configurable and tested; nothing calls it (ADR-0023).
- **F3, the daily database review.** The plan of 2026-08-23 stands and is not started.
- **The ingress.** F1 is polled; no inbound HTTP is needed until Jira webhooks are.
- **F0 cartography as a dependency.** M6's service map supplies the repository and commit an
  analysis needs. F0 summaries improve the report and do not gate it.
- **M5 evaluation.** It learned from Jira transitions; with no tickets it needs a different
  signal, and that signal is whether these reports get read. Not before there are reports.

## Open risks

- **Nothing says "again".** Recurrence and dedup do not run: `dedup_check` shortlists from
  `open_tickets_for_service` and only `create_ticket` writes it, so the third pod-down of a
  night reads exactly like the first. ADR-0023 is amended to say so out loud rather than to
  claim, as it first did, that recurrence "matters more, not less" — it decides that
  recurrence waits for Jira instead of building a second store of what was said. This is the
  release's weakest point and the one an on-call reader meets first, so it is a risk and not
  a closed question.

- **The alert class can contradict its own reasoning, and the class is what routes.** On
  2026-08-24 at 19:05 the classifier returned `saturation` while its own `why` named
  `crash_restart`. Both fields are `Filled` and the answer is well-formed, so no schema
  reaches it. The class chose the recipe: CPU was collected — and became a ranked hypothesis
  and two evidence lines — while the StatefulSet replica metrics were not. A silent
  mis-classification changes what the report is built from, in either direction. It argues
  the recipes are cut too finely as much as it argues for a validator.
- **Follow-up now parses, and asks for nothing.** The shape defect that discarded three runs'
  plans is fixed (`4f909dd`: the envelope arrived as a string, so the decode had to feed the
  peel). The first clean run then planned no calls at all — on a collection where `spans` is
  `not_instrumented` and the exit-code detail sits at namespace scope, which is the prompt's
  own worked example of when to ask. One empty plan is not a defect; an empty plan every time
  means the loop costs an `analysis` call per incident and buys nothing, and the report is
  poorer for what was never fetched.
- **Confidence may never exceed low even with analyses running.** Both live runs reached
  `low` because nothing was analysed; whether a real `code_analysis` substantiates a
  mechanism well enough for `medium` is untested and is the whole premise of Phase 3.
- **`strict` does not reach production until LiteLLM is upgraded** past v1.98.0 to the
  v1.99.0 line (ADR-0022). Until then `qualify` fails about half the time per call and leans
  on three attempts.
- **The infra track has never been exercised.** Phases 4 and 5 are the first contact with a
  real cluster and a real Platform for code written from API references.
