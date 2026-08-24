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
- [ ] 1.5 `docs/ticket-spec.md` is restated as the report spec, with the nine sections intact and the Jira workflow section marked postponed.

## Phase 2: the report is the product

- [ ] 2.1 The report renders every section the diagnosis carries — symptom with numbers and window, impact, probable cause with confidence, evidence with links, location as repository/commit/paths, expected change, out of scope, ruled out, unknowns — and omits nothing silently: a section with no content says why.
- [ ] 2.2 The location names the repository, the deployed commit and the IaC path, and carries the mapping rung: an image-derived commit and a `serves`-pattern one must not read alike (ADR-0019, ADR-0020).
- [ ] 2.3 Every message about one incident lands in the thread opened by `open_incident`, including the recurrence escalations (ADR-0003).
- [ ] 2.4 A report longer than Slack accepts is split at a section boundary, never mid-evidence, and the split is stated.
- [ ] 2.5 The four causes ruled out and the six unknowns produced by the 2026-08-24 run all appear in the rendered report — the fixture is that run, so the thing this milestone exists to deliver is what the test asserts.

## Phase 3: the analysis image

- [ ] 3.1 `docker/analysis/Dockerfile` builds an image that runs `triage.analysis.entrypoint` for a given `AnalysisRequest` and writes an `AnalysisResult`, with no Triage source on the path but the entrypoint and its schemas.
- [ ] 3.2 The image clones over https at the requested commit and refuses a ref it was not given.
- [ ] 3.3 Running the image locally against the 2026-08-24 hypotheses produces a `code_analysis` and an `iac_analysis` result — the first time either has ever run.
- [ ] 3.4 `diff_analysis` still has no entrypoint and still fails as a stated failure naming the kind; the diagnosis records it as an unknown and caps confidence (ADR-0014). This release does not add it.
- [ ] 3.5 The image is published to the infra account's registry and named in `config.analysis.job`.

## Phase 4: the sandbox

- [ ] 4.1 The Job template, the gVisor `RuntimeClass`, the `NetworkPolicy` (egress to GitHub and the registry only) and the read-only role the Job writes its result with exist as manifests under `deploy/`.
- [ ] 4.2 `KubernetesJobApi` submits one and reads its result against a real cluster — the first time it has spoken to one.
- [ ] 4.3 A Job that exceeds its deadline or its memory limit is reported as a stated failure, not a hang: the analysis fails, the diagnosis records why, and the report says so.
- [ ] 4.4 The Job's egress is verified to be refused everywhere it was not granted, by trying.

## Phase 5: it runs itself

- [ ] 5.1 The self-hosted LangGraph Platform runs the six registered graphs against the shared Postgres, with Triage's tables in the `triage` schema and checkpoints beside them.
- [ ] 5.2 The 60-second `alert_poller` cron creates runs, and a cycle that has not persisted for its gate creates none (ADR-0018).
- [ ] 5.3 Two alerts for the same monitor and firing group inside one cycle produce one run, not two.
- [ ] 5.4 A real production alert reaches a real Slack channel, analysed, without anyone running a command.

## Out of scope

- **Jira.** The path stays configurable and tested; nothing calls it (ADR-0023).
- **F3, the daily database review.** The plan of 2026-08-23 stands and is not started.
- **The ingress.** F1 is polled; no inbound HTTP is needed until Jira webhooks are.
- **F0 cartography as a dependency.** M6's service map supplies the repository and commit an
  analysis needs. F0 summaries improve the report and do not gate it.
- **M5 evaluation.** It learned from Jira transitions; with no tickets it needs a different
  signal, and that signal is whether these reports get read. Not before there are reports.

## Open risks

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
