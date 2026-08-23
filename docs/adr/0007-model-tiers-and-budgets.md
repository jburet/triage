# 0007 — Model tiers and spend guardrails

Status: Accepted, implemented. Resolves architecture open item 7.

## Decision

Graph code asks for a **tier**, never a model. Three aliases, resolved by the
LiteLLM proxy:

| Tier | Used by |
|---|---|
| `triage` | `classify_alert`, `dedup_check`, routing |
| `analysis` | `qualify`, `compose_ticket`, post-mortem, Agent SDK analyses |
| `diagnosis` | `diagnose`, `self_review`, F3 report |

Guardrails, both, enforced by the proxy: **500 k tokens per run** and **$50 per
day** globally.

Structured output uses `method="function_calling"`, not the library default of
`json_schema`.

## Why

**Tiers, not model names.** Changing which Anthropic model serves analysis becomes
a proxy config change rather than a code change, and no graph node can quietly
drift onto an expensive model. There is a test asserting each node's tier for
exactly that reason — a node drifting from `triage` to `diagnosis` is a silent
recurring bill, not a visible failure.

**Both caps, because they fail differently.** The per-run cap stops one pathological
run — a retry loop, a repository that summarises into millions of tokens — from
consuming the day's budget. The daily cap stops many normal runs from doing the
same thing collectively, which is what an alert storm looks like. Neither
substitutes for the other.

**`function_calling` over `json_schema`.** Behind this proxy every model is an
Anthropic one, where tool use is native; OpenAI-style `response_format` depends on
LiteLLM translating it. The default was chosen by the client library for a
different backend. When structured output fails to parse, `LiteLLMClient` raises
`StructuredOutputError` naming the tier and the schema, rather than returning
`None` into the graph — that failure was found by running the pipeline against a
stub proxy and is now covered by a test.

## Amended 2026-08-23 — a second client, for local runs

`AnthropicClient` calls the API directly with an API key, chosen by
`TRIAGE_LLM_PROVIDER` (`auto` takes it only when a key is set *and* the LiteLLM
URL is still the default, so a deployment that has a proxy keeps its guardrails).

The rule this ADR exists for is unchanged: graph code still asks for a tier, and
no model name appears under `src/`. The three ids come from `TRIAGE_MODEL_*`, and
an unset one is an error naming the variable rather than a default compiled in.
Structured output is still one forced tool call — deliberately the same mechanism
as the proxy path, because a local run that answered a different way would not be
evidence about the production one.

**What it does not have is the guardrails**, and that is the whole cost of it: no
per-run cap, no daily cap, no central log. It exists because the first live
one-shot could not run at all — there was no proxy on the machine, and "stand up
LiteLLM first" is how trying a real alert stops happening. Production stays on the
proxy.

Two things are deliberately not sent by the direct client: `temperature`, which
the current models reject outright, and `effort`, which is a per-model capability
the operator's choice of model decides — sending it to a model that does not take
it fails the run rather than costing a little more.

## Amended 2026-08-23 — the proxy runs locally too

`docker/litellm.yaml` plus the `litellm` service in `docker-compose.yml` are the
same three aliases on `localhost:4000`, started by `make proxy`. It resolves the
tiers from the *same* `TRIAGE_MODEL_*` variables the direct client reads, which
is the point: the two paths must differ in guardrails, not in which model
answers, or a local run through one says nothing about the other.

Two things this made explicit and are worth keeping written down.

**The daily cap needs a database.** With no `DATABASE_URL`, LiteLLM starts, logs
one warning, and enforces `max_budget` not at all — a proxy that looks like
production and silently is not. The compose service therefore gets its own small
Postgres, deliberately not Triage's: those are LiteLLM's ~150 Prisma migrations
and they are not Triage data.

**The per-run 500k cap is not in this config.** A run is a graph invocation
spanning many requests and LiteLLM budgets per key, not per caller, so enforcing
it would mean minting a virtual key per run. Local development does not need
that; the deployed proxy does, and this file is not it.

The `auto` rule is unchanged and now has a sharper edge: it prefers the direct
client as soon as a key is set, so `make proxy` alone does not route through the
proxy — `TRIAGE_LLM_PROVIDER=litellm` does. Left as an explicit choice rather
than a liveness probe on port 4000, because a provider that changes under you
depending on what happens to be running is worse than one you have to name.

## Revisit when

Someone runs production through the direct client because it was easier, which
shows up as a spend surprise rather than an error — the log line on every direct
run says the caps do not apply, and that is the only warning there is. Or the
daily cap is hit by legitimate load — raise it, but check first whether the
retry loop in the ticket pipeline is being exhausted, since three composes plus
three Opus reviews per ticket is the most likely cause.
