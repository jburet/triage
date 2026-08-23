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

## Revisit when

Someone runs production through the direct client because it was easier, which
shows up as a spend surprise rather than an error — the log line on every direct
run says the caps do not apply, and that is the only warning there is. Or the
daily cap is hit by legitimate load — raise it, but check first whether the
retry loop in the ticket pipeline is being exhausted, since three composes plus
three Opus reviews per ticket is the most likely cause.
