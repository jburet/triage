# Triage

Turns a production alert or signal into **a ticket a developer can act on without
further investigation.**

Triage is not an AI SRE. It is the layer between observability (Datadog) and the
development team: it reads the code, the infrastructure-as-code and the database,
and produces a precise, evidence-backed work item.

- **Read-only on production systems** — cluster, databases, observability stack.
- **Writes only to Slack and Jira.** Never to Git, never to infrastructure.
- **Analysis only.** Triage diagnoses and specifies; fixing is the developer's job.
- **Never invents.** Anything that cannot be filled with confidence is marked
  unknown, with the reason — enforced by the schemas, not by prompt instruction.

See [`docs/roadmap.md`](docs/roadmap.md) for the product, [`docs/architecture.md`](docs/architecture.md)
for the system, [`docs/ticket-spec.md`](docs/ticket-spec.md) for what a finished ticket
must contain, and [`docs/adr/`](docs/adr/) for the decisions behind both.

## Status

**M1 — the shared ticket pipeline.** The sub-graph that turns a `Diagnosis` into a
Jira ticket is implemented and tested end to end against fixtures. The collectors
that produce a `Diagnosis` (F0 cartography, F1 incidents, F3 database review) are
not built yet; see the milestone table in `docs/architecture.md`.

## Quick start

```bash
make dev            # uv sync, start Postgres, apply migrations
make test           # full suite — no network, no model spend
make run-fixture    # run the pipeline on a fixture diagnosis, in dry-run mode
```

`make run-fixture` needs neither a database nor credentials. With
`TRIAGE_DRY_RUN=1` (the default) Jira and Slack are recording fakes and state is
in-memory, so the script prints the Jira payload and Slack messages that *would*
have been sent.

Pick a different fixture with `make run-fixture FIXTURE=tests/fixtures/diagnoses/<name>.json`.

## Layout

| Path | What lives there |
|---|---|
| `src/triage/schemas/` | The executable form of the ticket specification |
| `src/triage/prompts/` | Prompt templates, versioned as files so changes are reviewable |
| `src/triage/nodes/` | One module per graph node |
| `src/triage/graphs/` | Graph wiring and state |
| `src/triage/integrations/` | Jira, Slack and the rest — protocol, real client, fake |
| `src/triage/db/` | Models and the repository the nodes actually depend on |
| `evals/` | Scored fixture suite; spends money, so it is not in CI |

## Models

Graph code asks for a **tier** — `triage`, `analysis` or `diagnosis` — never for a
model. The tier-to-model mapping and the budget guardrails live in the LiteLLM
proxy configuration (ADR-0007). No model name appears anywhere under `src/`.

## Running the graph in LangGraph Studio

```bash
langgraph dev
```

`langgraph.json` registers `ticket_pipeline`. Studio invokes it with no injected
dependencies, so it falls back to `build_deps()` and therefore to dry-run defaults
unless `TRIAGE_DRY_RUN=0` is set.
