# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Triage turns a production signal (Datadog alert, daily DB review) into a Jira ticket a
developer can act on without further investigation. It is **read-only on every production
system, writes only to Slack and Jira, and never invents** — an unfillable field is an
`Unknown` with a reason, enforced by the Pydantic schemas rather than by prompt text.

Current state (see `docs/architecture.md` §10): only **M1, the shared ticket pipeline**
(`Diagnosis` → Jira ticket / Slack notice) exists. Collectors (F0 cartography, F1 incidents,
F3 DB review), the Analysis sub-graph, the FastAPI ingress and the infra track are not built.
Design docs: `docs/roadmap.md` (product), `docs/architecture.md` (system),
`docs/ticket-spec.md` (what a finished ticket must contain), `docs/adr/` (decisions, each
with the condition that would make it wrong — add an ADR when changing one).

## Commands

Python 3.12, `uv`-managed. CI (`.github/workflows/ci.yml`) runs exactly `make lint` + `make test`.

```bash
make dev                 # uv sync --all-extras, create .env, start Postgres (docker compose), alembic upgrade head
make lint                # ruff check + ruff format --check (src tests evals scripts) + mypy --strict
make test                # uv run pytest -q  — offline: no DB, no network, no model spend
uv run pytest tests/integration/test_ticket_pipeline.py -k oom      # single file / test
uv run ruff format src tests evals scripts                          # apply formatting
make run-fixture [FIXTURE=tests/fixtures/diagnoses/<name>.json]     # run pipeline on a fixture; calls real model tiers via LiteLLM, Jira/Slack faked
make capture-datadog ARGS="find 'pod down'"   # then `triggers <id>`, then
                         # `capture <id> <iso-time> --slug <name> --scope service:<x>`; read-only,
                         # writes tests/fixtures/datadog/<slug>/, needs a Datadog key
make evals               # scored fixture suite against real models — spends money, not in CI
langgraph dev            # LangGraph Studio; langgraph.json registers `ticket_pipeline`
make migrate             # alembic upgrade head
```

`TRIAGE_DRY_RUN=1` is the default: Jira/Slack are recording fakes and the repository is
in-memory. Secrets come from `.env` with the `TRIAGE_` prefix (`src/triage/config.py`
`Settings`); static config is `config.yaml` (`Config`).

## Architecture

**Graph wiring** — `src/triage/graphs/ticket_pipeline.py` builds a LangGraph `StateGraph`
over `TicketPipelineState` (`graphs/state.py`, a `total=False` TypedDict whose only input
is `diagnosis`). One module per node under `src/triage/nodes/`; routing functions live in
the graph module. Flow: `record_diagnosis → dedup_check → (update_existing_ticket | confidence_gate) → (notify_below_threshold | compose_ticket → self_review → create_ticket | retry | notify_review_exhausted)`.
The compose/review retry budget counts composes (`thresholds.max_compose_attempts`).

**Dependency injection** — nodes are plain async functions; collaborators arrive as a
`Deps` dataclass (`runtime.py`) in `RunnableConfig["configurable"]["deps"]`, read via
`deps_from_runnable_config(config)`. `build_deps()` picks real vs fake from `dry_run` and is
the fallback when nothing injects deps (Studio). Never reach for module-level clients.

**LLM access by tier only** — `llm.py` exposes `StructuredLLM.call(tier, prompt, schema)`
with `tier ∈ {"triage", "analysis", "diagnosis"}` (Haiku/Sonnet/Opus, mapped in the LiteLLM
proxy, ADR-0007). **No model name may appear under `src/`.** Every call returns a validated
Pydantic model via `function_calling` (tool use), never `response_format`. Prose-only output
still goes through a schema with a prose field.

**Prompts as files** — `src/triage/prompts/*.md`, loaded with `prompts.render(name, **sections)`:
instructions first, then each input as a `<tag>…</tag>` JSON block (never string
interpolation — inputs are model-generated prose).

**Schemas = the spec** — `src/triage/schemas/`. `common.py` defines `Filled` (rejects
placeholders like "N/A", "TBD", "unknown") and `Unknown{reason}`; `MaybeUnknown = Filled | Unknown`.
`TicketDraft` mirrors the nine sections of `docs/ticket-spec.md`. `Confidence` is a
three-level enum, deliberately not a number (ADR-0002).

**Integrations** — `integrations/base.py` holds the `JiraClient`/`SlackClient` protocols and
their recording fakes; `jira.py` (REST v3 over httpx, basic auth email+token, ADR-0013,
unverified against a live server), `slack.py`, `adf.py` (Atlassian Document Format
rendering). A ticket key the model was not shown is discarded at dedup.

**Persistence** — nodes depend on the `TriageRepository` protocol (`db/repo.py`), never on
the ORM; `InMemoryRepository` is used in tests and dry-run, `SqlRepository` otherwise.
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
`latency_low_confidence` = Slack-notice path); `tests/integration/` runs the whole graph
against them, `tests/unit/` covers schemas, rules, ADF and LLM plumbing. Tests must stay
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
