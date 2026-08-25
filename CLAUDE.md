# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Triage turns a production signal (Datadog alert, daily DB review) into a Jira ticket a
developer can act on without further investigation. It is **read-only on every production
system, writes only to Slack and Jira, and never invents** — an unfillable field is an
`Unknown` with a reason, enforced by the Pydantic schemas rather than by prompt text.

Current state (see `docs/architecture.md` §10): **M1 (ticket pipeline), M2 (F0 cartography),
M3 (Analysis sub-graph, F1 incidents, Datadog collection, alert poller), M6 (the service
map: workload → repository → deployed commit → chart path) and M8 (F2, a recurring code
exception becomes a threaded report)** exist in code. Only Datadog has ever been read live —
`make run-mapping`, `make run-incident` and `make run-errors` against the real org; Jira,
GitHub, Slack and Kubernetes have never been spoken to, and every test replays a fixture.
Against the shipped example `config.yaml` the mapping resolves a repository and no commit,
because no real Zeenea repository is declared: declaring them is the prerequisite for M6
being worth running. **No analysis has ever read a repository** — the investigative kinds
have no deployed image (M7 3.4) — so every F1 and F2 report today is honest about having
read no code, and F2's whole point, the file and function Datadog names, is proven against
fixtures and unobserved against a tree. F3 (daily DB review), the FastAPI ingress, the
analysis Job image, and the whole infra track are not built.
Design docs: `docs/roadmap.md` (product), `docs/architecture.md` (system),
`docs/ticket-spec.md` (what a finished report must contain), `docs/adr/` (decisions, each
with the condition that would make it wrong — add an ADR when changing one).

## Commands

Python 3.12, `uv`-managed. CI (`.github/workflows/ci.yml`) runs exactly `make lint` + `make test`.

```bash
make dev                 # uv sync --all-extras, create .env, start Postgres (docker compose), alembic upgrade head
make proxy               # local LiteLLM on :4000 from docker/litellm.yaml — the three tier
                         # aliases and the daily cap; needs TRIAGE_LLM_PROVIDER=litellm to be used
make lint                # ruff check + ruff format --check (src tests evals scripts) + mypy --strict
make test                # uv run pytest -q  — offline: no DB, no network, no model spend
uv run pytest tests/integration/test_ticket_pipeline.py -k oom      # single file / test
uv run ruff format src tests evals scripts                          # apply formatting
make run-fixture [FIXTURE=tests/fixtures/diagnoses/<name>.json]     # run pipeline on a fixture; calls real model tiers via LiteLLM, Jira/Slack faked
make run-mapping [ARGS="--days 14 --db plt-hcl-software-uat"]   # derive the service map from
                         # real Datadog events and print the pass's report; read-only, no model call
make run-errors [ARGS="--hours 72 --analyse"]   # tick the hourly F2 poller by hand; the tick
                         # is read-only Datadog and costs nothing, `--analyse` drives every gated
                         # group through the code_exception graph and calls the real tiers
make capture-errors      # capture one hour of the org's Error Tracking issues as fixtures
make repository-map      # regenerate config/repository-map.yaml from the architecture document
make capture-datadog ARGS="find 'pod down'"   # then `triggers <id>`, then
                         # `capture <id> <iso-time> --slug <name> --scope service:<x>`; read-only,
                         # writes tests/fixtures/datadog/<slug>/, needs a Datadog key
make evals               # scored fixture suite against real models — spends money, not in CI
make evals-incident      # scores F1 classification and qualification on the captured alert
make capture-datadog ARGS="capture <monitor> <iso-time> --slug <name> --scope service:x"
langgraph dev            # LangGraph Studio; langgraph.json registers `ticket_pipeline`
make migrate             # alembic upgrade head
```

`TRIAGE_DRY_RUN=1` is the default: Jira/Slack are recording fakes and the repository is
in-memory. Secrets come from `.env` with the `TRIAGE_` prefix (`src/triage/config.py`
`Settings`); static config is `config.yaml` (`Config`).

## Architecture

**Graph wiring** — one `StateGraph` per graph in `src/triage/graphs/`, one state TypedDict
each in `graphs/state.py` (all `total=False`), one module per node under `src/triage/nodes/`,
routing functions in the graph module. `langgraph.json` registers eight:

- `ticket_pipeline` — `record_diagnosis → dedup_check → (update_existing_ticket | confidence_gate) → (notify_below_threshold | compose_ticket → self_review → create_ticket | retry | notify_review_exhausted)`. The retry budget counts composes (`thresholds.max_compose_attempts`).
- `cartography` — F0; see ADR-0006, ADR-0015.
- `analysis` — `select_hypotheses → run_analyses → diagnose`. Shared by F1 and F3.
- `incident` — F1: `open_incident → classify_alert → collect → follow_up ⟲ → qualify → [analysis] → [ticket_pipeline] → draft_postmortem? → settle_signal`. `IncidentState` inherits `AnalysisState` and `TicketPipelineState` so both compiled sub-graphs can be added as nodes.
- `code_exception` — M8/F2: `open_group → collect_exception → qualify_exception → [analysis] → [ticket_pipeline] → settle_group`. `CodeExceptionState` inherits both sub-graph states, as `IncidentState` does. No `classify_alert` — the issue already names its type and its source location — and no post-mortem, which is an incident's write-up.
- `alert_poller` — one tick of `poll_alerts`; the 60-second cron is a Platform object.
- `error_poller` — M8/F2: `poll_error_issues → group_error_issues`, hourly. Reads Datadog
  Error Tracking, keeps only what was first seen or regressed in the window, then collapses
  those into groups and gates them on volume; see ADR-0025, ADR-0026. No model call anywhere
  on it. A node module must **not** carry `from __future__ import annotations` — it
  stringifies the `config` annotation, LangGraph then passes no config, and every node
  silently falls back to `build_deps()`.
- `service_mapping` — M6: `select_services → derive_workloads → persist_workloads → report_mapping`. No model call anywhere on it; see ADR-0019, ADR-0020.

**Dependency injection** — nodes are plain async functions; collaborators arrive as a
`Deps` dataclass (`runtime.py`) in `RunnableConfig["configurable"]["deps"]`, read via
`deps_from_runnable_config(config)`. `build_deps()` picks real vs fake from `dry_run` and is
the fallback when nothing injects deps (Studio). Never reach for module-level clients.

**LLM access by tier only** — `llm.py` exposes `StructuredLLM.call(tier, prompt, schema)`
with `tier ∈ {"triage", "analysis", "diagnosis"}` (ADR-0007). **No model name may appear
under `src/`.** Two interchangeable implementations, chosen by `TRIAGE_LLM_PROVIDER`
(`auto` by default): `LiteLLMClient` through the proxy, which resolves the aliases and
enforces the spend caps — this is production — and `AnthropicClient` straight to the API
with `TRIAGE_ANTHROPIC_API_KEY`, for local runs where standing up a proxy is why the alert
never gets tried. The direct client reads its three model ids from `TRIAGE_MODEL_*`, so the
rule holds; it has no spend caps, which is its whole cost. `make proxy` runs the proxy
locally (`docker/litellm.yaml`, same aliases, same `TRIAGE_MODEL_*`, own Postgres for the
spend it counts) — so the two paths differ in guardrails, not in which model answers. Through
a proxy the tier is sent as the model name; a shared proxy that publishes its own names is
reached by filling all three `TRIAGE_MODEL_*` (all or none — half is refused at startup). Both
return a validated Pydantic
model from one forced tool call, never `response_format`. Prose-only output still goes
through a schema with a prose field.

**Prompts as files** — `src/triage/prompts/*.md`, loaded with `prompts.render(name, **sections)`:
instructions first, then each input as a `<tag>…</tag>` JSON block (never string
interpolation — inputs are model-generated prose).

**Schemas = the spec** — `src/triage/schemas/`. `common.py` defines `Filled` (rejects
placeholders like "N/A", "TBD", "unknown") and `Unknown{reason}`; `MaybeUnknown = Filled | Unknown`.
`TicketDraft` mirrors the nine sections of `docs/ticket-spec.md`. `Confidence` is a
three-level enum, deliberately not a number (ADR-0002).

**The reports** — `src/triage/report.py` is the delivery (ADR-0023) and is a rule, not a
model call. `render_incident` is F1's; `render_code_exception` is F2's sibling and shares
every section helper with it, adding an exception header, a commit line that is F2's own
choice rather than the map's, and the telemetry that was searched for and discarded.
`publish_report` picks between them on what the calling feature left in the state.

**Integrations** — `integrations/base.py` holds the `JiraClient`/`SlackClient` protocols and
their recording fakes; `jira.py` (REST v3 over httpx, basic auth email+token, ADR-0013),
`slack.py` (every notice about one incident carries `thread_ts`), `adf.py`, `github.py`,
`datadog.py` (six read-only REST calls, per-endpoint concurrency gates for the measured rate
limits, `FakeDatadogClient` replaying `tests/fixtures/datadog/`), `platform.py` (creates runs
on the LangGraph Platform; absent → in-process, ADR-0011). All are unverified against live
services. A ticket key the model was not shown is discarded at dedup.

**The service map** — `src/triage/mapping/` is pure functions joining what is already
structured, so the derivation is a rule and never a tier call: `seed.py` (the architecture
document's repository table, generated into `config/repository-map.yaml` by
`make repository-map`), `images.py` and `derive.py` (the running image names the
repository — a name no repository claims, `plt-hcl-software-uat`, is what F0's map cannot
key on), `commits.py` (the build number is a GitHub tag; failing that the default branch,
which is never presented as the deployed commit — the two confidence caps and their caveats
live here), `iac.py` (which chart in the IaC repository defines *this* workload),
`resolve.py` (unclaimed repositories, the mono-tenancy naming rule), `report.py` (what the
pass could and could not attribute). `scope.deployed_repo` consults the derived workload,
then F0's map, then `config.yaml`'s `serves` patterns, and records which of the three
answered.

**F1 collection** — `src/triage/collect/` is pure functions over Datadog responses, with no
graph knowledge: `recipes.py` (window rule, alert-class recipes, the monitor-query idiom),
`reduce.py` (log templating, event diffing, timeseries downsampling), `sweep.py` (the fixed
sweep, the emptiness widening, the bounded follow-up loop), `budget.py` (fit to
`collection.max_prompt_bytes`, stating every cut). `src/triage/scope.py` resolves an alert to
a team by glob pattern and to an environment through the cluster map — never from an `env:`
tag, which no alert carries usefully (ADR-0017).

**F2 code exceptions** — `src/triage/errors/` is pure functions over Error Tracking, the
same shape as `triage.collect`: `issues.py` (parse the envelope, the code-exception rule,
new-or-regressed), `grouping.py` (the group key — exception type, source location and **the
repository the mono-tenancy rule resolves**, never the message, ADR-0026), `gate.py` (the
per-tick floor, the cumulative escalation, the per-tick cap, and the `reanalyse_after`
cooldown that keeps a 10,000-an-hour group from being reposted every tick), `sweep.py` (the
three collectors, and which kind of nothing each found — ADR-0027), `paths.py` (Datadog's
`file_path` is a fully-qualified class name and its `function_name` a JVM symbol; both are
converted by convention and the conversion is stated — ADR-0028), `versions.py` (the commit
the version an exception was first seen on names, the fallback when nothing claims it, and
the release-boundary hypothesis). Every one of these is a rule; nothing here asks a model
anything. The one tier call on the F2 path is `qualify_exception`, which fills the
*existing* `Qualification` so the Analysis sub-graph is untouched.

**Persistence** — nodes depend on the `TriageRepository` protocol (`db/repo.py`), never on
the ORM; a `workloads` row is one running service joined to the repository whose code it
runs (M6); an `error_groups` row is one code-exception defect across every tenant that raises it, keyed
on the grouping rule's own output so a later tick finds it by recomputing the key, and
carrying the cumulative count the escalation reads and the Slack thread every message about
it replies under (M8, ADR-0026: `open → analysing → reported`, plus `unmapped` for a service
no repository claims); a `signals` row is one alert *cycle* (monitor, firing group, duration,
recovery) and its status carries the persistence gate — `received → waiting → analysing → diagnosed →
ticketed|discarded`, with the terminal `self_recovered` and `out_of_scope` (ADR-0018); `InMemoryRepository` is used in tests and dry-run, `SqlRepository` otherwise.
Tables live in a dedicated `triage` Postgres schema (shared DB with LangGraph Platform
checkpoints, which Triage never touches). Migrations under `db/migrations/` must stay
scoped to `triage.`; `tests/unit/test_migrations.py` renders `alembic upgrade head --sql`
and checks every model table/column appears — add a migration whenever `db/models.py` changes.

## Testing conventions

`tests/conftest.py` is the toolkit: `build_deps(config, dedup=…, drafts=…, verdicts=…)`
assembles a `Deps` of fakes, `run_config(deps)` wraps it for `graph.ainvoke`. `FakeLLM`
responses are keyed by the *schema* the node asks for; a sequence is consumed call by call
with the last element repeating ("fail twice then pass" = `[a_verdict(False), a_verdict(False), a_verdict()]`).
Fixture diagnoses live in `tests/fixtures/diagnoses/*.json` (`oom_payments` = ticket path,
`latency_low_confidence` = Slack-notice path). `tests/fixtures/datadog/hcl_software_uat_20260822/`
is one real incident captured by hand on 2026-08-23 — the alert that settled ADR-0016, 0017
and 0018 — and every number the collection tests assert was measured on it. Datadog retains
logs and spans about fifteen days, so it **cannot be re-captured**: treat those files as the
permanent record they are. `conftest` replays it through `fake_datadog()`, `captured_alert()`
and `pod_down_alert()`. `tests/integration/` runs whole graphs, `tests/unit/` covers schemas,
rules, reduction, ADF and LLM plumbing. Tests must stay
offline; anything that spends money belongs in `evals/`.

## Working conventions

- **Comments: as few as possible.** Module docstrings stating *why* a module exists are the
  one exception; no inline narration, no restating what the code does.
- **A milestone plan is developed in its own git worktree, on its own branch, and lands as a
  PR into `main`.** Several Claude sessions work this repo at once; one shared working tree
  means one session stages another's half-finished edits, which is how M2's commits swept up
  in-progress Datadog changes to `config.py` and `timings.md`.

      git worktree add -b m3-analysis ../triage-worktrees/m3 main
      cp .env ../triage-worktrees/m3/.env    # gitignored, so a new worktree has none
      cd ../triage-worktrees/m3 && uv sync --all-extras

  Commit after each plan behaviour and push the branch; open the PR when the plan's phases are
  green. Anything smaller than a plan — a doc fix, a one-line correction — still goes straight
  to `main` in the primary tree. Remove the worktree once the PR is merged
  (`git worktree remove`).
- **Measure and minimise time.** For every plan phase and every tool run (`make lint`,
  `make test`, `make run-fixture`, `make evals`, …) record wall-clock duration in
  `docs/plans/timings.md` (one line: date, plan/phase, command, seconds). Treat a rising number
  as a defect: prefer targeted `pytest <file>` over the full suite while iterating, run the full
  `make lint && make test` once per behaviour, and never run `make evals` from a test loop.

## Style

Ruff (line length 100, rules E/F/I/UP/B/SIM/RUF/ANN/PT; annotations required in `src/`),
mypy `--strict` with the pydantic plugin. Use explicit `TypeAlias`, not PEP 695 `type`
(UP040 is ignored on purpose). Module docstrings explain *why* a module exists and the
decision behind it — keep that habit.
