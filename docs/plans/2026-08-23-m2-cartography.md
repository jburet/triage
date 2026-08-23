# Plan: M2 — F0 cartography and the analysis Job contract (2026-08-23)

Architecture §2.5, §7; ADR-0006, ADR-0009. First of four plans (M2 → M5); M3 depends on
Phase 1 and Phase 3 of this one.

## Public interface

- `triage.analysis.runner.AnalysisRunner` (protocol) — `run(request: AnalysisRequest) -> AnalysisResult`.
  One method, async. `AnalysisRequest` names a `kind` (`summarize_repo`, `summarize_terraform`,
  `code_analysis`, `iac_analysis`, `diff_analysis`), a repo URL, one or two commits, and a
  free-form `question`. `AnalysisResult` carries `status`, a structured `result` payload typed
  per kind, and an `error`. Implementations: `FakeAnalysisRunner` (canned results keyed by kind),
  `LocalAnalysisRunner` (subprocess on the host, for dev and evals), `KubernetesJobRunner`
  (creates the gVisor Job, polls `triage.analysis_results`, deletes the Job — ADR-0009).
- `triage.schemas.system_map`: `RepoSummary`, `TerraformSummary`, `ServiceEntry`, `SystemMap`.
  Every prose field is `Filled` or `MaybeUnknown`, as in the existing schemas.
- `triage.db.repo.TriageRepository` gains `upsert_system_map_entries(...)`,
  `system_map_for_service(service)`, `last_summarised_commit(repo_url)`.
- `triage.graphs.cartography`: graph `cartography` registered in `langgraph.json`. Input state
  is either a full `repo_list` or a single `merge_event` (repo URL + commit).
- `Deps` gains `runner: AnalysisRunner`.
- A runnable `scripts/run_cartography.py` mirroring `run_fixture.py`.

## Phase 1: analysis Job contract

- [x] 1.1 A node can submit an `AnalysisRequest` and receive a validated `AnalysisResult` through `Deps.runner`, with the fake returning the canned result for that kind.
- [x] 1.2 `LocalAnalysisRunner` clones the repo at the requested commit with depth 1 into a throwaway directory, runs the analysis entrypoint there, and the directory is gone afterwards — including when the entrypoint fails.
- [x] 1.3 `KubernetesJobRunner` returns the row from `triage.analysis_results` matching the Job name once its status is terminal, and reports a failed result (not an exception) when the 15-minute timeout elapses or the Job errors.
- [x] 1.4 A result payload that does not validate against the schema for its kind is reported as a failed `AnalysisResult` naming the kind, never as a partial success.

## Phase 2: repository and Terraform summaries

- [x] 2.1 Summarising an application repo yields a `RepoSummary` with languages, frameworks, entry points, endpoints, inter-service dependencies, database access patterns and observability setup — every one either filled or an explicit `Unknown` with a reason.
- [x] 2.2 Summarising a Terraform repo yields a `TerraformSummary` listing resources, sizing, networking, managed databases and a module ↔ service mapping, from code only (no state is read).
- [x] 2.3 Both summaries are produced by the `analysis` tier and pass `evals/` scoring on at least one real public repo each (evals, not CI).
      Suite is `evals/cartography.py` (`make evals-cartography`); **unrun** — it needs network and spend.

## Phase 3: system map

- [ ] 3.1 Running `cartography` over the `config.yaml` repo list persists one `system_map` row per service and per Terraform module, keyed by `(kind, name)`, carrying the owning team from `config.yaml` and the summarised commit.
- [ ] 3.2 Re-running over the same commits is idempotent: rows are updated in place, never duplicated.
- [ ] 3.3 `system_map_for_service("payments-api")` returns the repo, team, entry points and Terraform resources a later feature needs to build a `Location`.
- [ ] 3.4 A service whose team is not declared in `config.yaml` is persisted with `team = None` and produces a Slack notice to the platform channel, rather than failing the run.

## Phase 4: incremental refresh (ADR-0006)

- [ ] 4.1 A `merge_event` for a repo re-summarises only the areas touched between the last summarised commit and the new one, and updates `source_commit`.
- [ ] 4.2 A `merge_event` for a repo with no prior summary falls back to a full summary of that repo.
- [ ] 4.3 A run flagged `full=True` re-summarises every repo regardless of diff; this is the entrypoint the weekly cron will call.

## Out of scope

- The Kubernetes Job manifest, gVisor runtime class, NetworkPolicy and the narrow DB role — infra track, tracked as a checklist in `docs/plans/2026-08-23-infra-track.md` (not yet written).
- The GitHub merge webhook endpoint — lands with the ingress in M3.
- Platform cron registration for the weekly full pass — infra track.

## Open risks

- The Claude Agent SDK inside the Job is assumed to be able to emit a JSON document matching the per-kind schema. If it cannot reliably, Phase 2 needs a second `analysis`-tier call to coerce prose into the schema, and 1.4 is the guard that surfaces it.
- ADR-0006's diff heuristic ("touched areas") is undefined. Plan assumes "files changed → their top-level package"; if that misses dependencies, the ADR says fix the invalidation rule, not the cadence.
