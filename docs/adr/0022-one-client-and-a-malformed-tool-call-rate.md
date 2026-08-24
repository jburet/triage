# 0022 — One client for both paths, and a measured malformed-tool-call rate

Status: Proposed. Supersedes the OpenAI-shaped proxy transport; records an unresolved defect.

## Decision

`LiteLLMClient` extends `AnthropicClient` and inherits its `call` unchanged. The proxy is
addressed with the **Anthropic** request shape at its own host — LiteLLM serves both
protocols, so the aliases resolve and the spend caps apply exactly as before. `langchain-openai`
leaves the hot path.

A structured field that arrives as *text* is decoded rather than refused: if the schema
declares a field as anything but `str` and the value is a string whose leading JSON value
parses, that value is used and any trailing markup dropped. This is parsing, not repair —
nothing is invented, the decode must succeed on its own, and the schema still refuses
whatever comes out.

`qualify` asks up to three times, each retry carrying the required *shape*, and hands the
collection to the team on Slack when all three fail.

## Why

The `llm.py` docstring claimed the two clients were "interchangeable by construction" and
"the same mechanism", and they were not: the proxy went through
`with_structured_output(method="function_calling")`, where tool arguments are a JSON string
LiteLLM rebuilds from what the model returned, while the direct client read a native
`tool_use` block whose `input` is already an object. A local reproduction could therefore
never have been evidence about production, which is the entire stated reason for keeping
them alike. That is fixed here on its own merit.

It is **not** fixed as a cure. On 2026-08-24 the first live F1 run died at `qualify`, and
the same prompt was then measured eight times per variant against the real proxy:

| Variant | Valid `Qualification` |
|---|---|
| OpenAI-shaped (what production ran) | 4/8 |
| Anthropic-native, raw | 6/8 |
| Anthropic-native, via the shipped client | 4/8 |
| `causes`-only schema (no `summary`) | 4/8 |
| `causes` before `summary` | 1/1 |
| `summary` with a length cap | 0/1 — the cap itself fails validation |
| Prompt sections fenced instead of `<tag>`-wrapped | 4/8 vs 3/8 tagged |
| Native + `strict: true` | 0/8 — the proxy rejects the field |

Read honestly: **nothing tried changed the rate.** 6/8 was noise; the same client measured
4/8 on the next run. About one call in two comes back malformed, and it is independent of
transport, of schema shape and field order, and of how prompt sections are delimited.

What the failures carry is the tell. The values hold tool-call markup cut out of the
model's answer by a partial parser — `…during the window.</summary>\n<causes">`,
`\n<parameter name="summary">placeholder`, `[{…}]</causes>`. `<parameter name=…>` is the
*text* form of a tool call. A model emitting it is not making a native tool call at all,
which points at the route rather than at the model: a ~50% malformed-tool-call rate from
Sonnet 5 is not ordinary API behaviour.

`strict: true` would end the argument — the API then guarantees `tool_use.input` validates
against the schema — and this proxy refuses the field outright
(`tools.0.custom.strict: Extra inputs are not permitted`), which is a LiteLLM version
limitation and the single most valuable thing to fix upstream.

Three attempts rather than two is arithmetic on the measured rate: at one failure in two,
two asks lose a run in four and three lose one in eight.

## Consequences

- `langchain-openai` is no longer used for model access; `ChatOpenAI`, `disabled_params`
  and the `parallel_tool_calls` workaround are gone with it. The Bedrock incompatibility
  that workaround existed for is recorded here rather than in a test of a dead path.
- Both clients now differ only in `base_url`, `api_key` and whether an unmapped tier falls
  back to its own name. Which model serves a tier is still configuration (ADR-0007).
- A malformed qualification costs three `analysis` calls before the team is told, and the
  team is told with the collection attached rather than not at all.

## What this does not fix

The rate. One incident in eight still ends with no analysis, and every other tier call on
this route carries the same risk unmeasured — `follow_up` was seen failing the same way on
2026-08-24, its `requests` list arriving as the string `'{"requests":[]}'`.

## Revisit when

The same prompt is measured against the Anthropic API directly, with a key, bypassing the
proxy and Bedrock. That is the one variant not tried and the one that separates "the model
does this" from "this route does this". If direct comes back clean, this belongs to
whoever runs `litellm-euw3`, and the decode and the third attempt should be deleted rather
than kept as folklore. If direct fails too, the schema is asking for too much in one call
and `qualify` should be split.
