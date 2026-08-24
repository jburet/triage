# 0022 — One client for both paths, and strict tool use where a schema can say it

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

Tool use is **strict** wherever the schema can express it — every object fully required, so
that listing every property changes nothing the node asked for. The value constraints strict
rejects (`minimum`, `minItems`, …) leave the wire and stay in the Pydantic schema: the API
guarantees the structure, `model_validate` still refuses a `rank_score` of 2. A proxy too old
to know the field rejects the whole request, so the client offers strict, notices that
refusal once, and asks without it thereafter — the proxy gets the fix the day it is upgraded,
with no configuration.

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
| OpenAI-shaped through the proxy (what production ran) | 4/8 |
| Anthropic-native through the proxy, raw | 6/8 |
| Anthropic-native through the proxy, shipped client | 4/8 |
| **Direct to the API, no proxy, no Bedrock** | **3/8** |
| `causes`-only schema (no `summary`) | 4/8 |
| `summary` with a length cap | the cap itself fails validation |
| Prompt sections fenced instead of `<tag>`-wrapped | 4/8 vs 3/8 tagged |
| Prompt reframed as "call the tool" rather than "write two things" | 2/6 |
| **Direct + `strict: true`** | **6/6 raw; 6/8 and 7/8 through the shipped client** |
| Proxy + `strict: true` | rejected: `tools.0.custom.strict: Extra inputs are not permitted` |
| Proxy, shipped client (offers strict, is refused, asks again without) | 3/8 and 4/8 — the unstrict rate, as expected |

Two things fall out of that table, and the first one killed the theory this ADR was
originally written around.

**The route is not at fault.** Direct to the API, no proxy and no Bedrock in the path,
the same prompt scored 3/8 — indistinguishable from the proxy's 4/8, with the identical
signature: the whole answer serialised into `summary`, ending `…\n</Qualification>\n\n`.
The leaked markup (`<parameter name="summary">`, `</causes>`, `</Qualification>`) is the
*text* form of a tool call, and the model emits it just as often with nothing between it and
the API. Transport, schema shape, field order and prompt delimiters were each measured and
each changed nothing.

**Strict is what changes it.** It is the one thing that makes the API guarantee the tool
input matches the schema rather than leaving it to the model, and it took the same prompt
from 3/8 to 6/6. It is not a repair applied after the fact and it invents nothing: it
constrains generation.

Three attempts rather than two is arithmetic on what is left. Strict does not reach every
schema — `FollowUpPlan`, `TicketDraft` and `DiagnosisDraft` all carry optional fields, and
forcing those into `required` would ask the model for something the node did not — so the
unstrict schemas keep the old rate and the retries carry them.

## Consequences

- `langchain-openai` is no longer used for model access; `ChatOpenAI`, `disabled_params`
  and the `parallel_tool_calls` workaround are gone with it. The Bedrock incompatibility
  that workaround existed for is recorded here rather than in a test of a dead path.
- Both clients now differ only in `base_url`, `api_key` and whether an unmapped tier falls
  back to its own name. Which model serves a tier is still configuration (ADR-0007).
- A malformed qualification costs three `analysis` calls before the team is told, and the
  team is told with the collection attached rather than not at all.

## What this does not fix

Every schema. Strict reaches `Qualification` and `AlertClassification`; `FollowUpPlan`,
`ReviewVerdict`, `TicketDraft`, `DiagnosisDraft` and `DedupDecision` all carry an optional
field somewhere and are sent as they are. `follow_up` was seen failing exactly this way on
2026-08-24, its `requests` list arriving as the string `'{"requests":[]}'`, and nothing here
stops that recurring — the decode catches that particular shape, and the rate for those
schemas is unmeasured.

And production, until the proxy takes the field. `litellm-euw3` rejects `strict` outright,
so the path that matters keeps the old rate and the three attempts behind it. Upgrading
LiteLLM is now the single highest-value thing available, and it needs no change here.

The fix upstream is [PR #36979](https://github.com/BerriAI/litellm/pull/36979), *"fix(anthropic):
preserve optional Responses tool properties"*, merged 2026-08-17: it adds `strict` to
`AnthropicMessagesTool` — the type whose validation produces the refusal we get — and carries
it through the pass-through adapters. It is **not** in v1.98.0, the current stable; it is in
the v1.99.0 line from `v1.99.0-dev.1` onwards, including `v1.99.0-rc.1`. So: **deploy v1.99.0
once it is stable**, or the rc if that is acceptable.

One operational note for that day: whether strict is offered is remembered per client
instance, for the life of the process. A Triage already running against the old proxy has
learned that strict is refused and will keep asking without it — the deployment has to be
restarted after the proxy is, or the upgrade shows no effect at all.

## Revisit when

The proxy accepts `strict`. Measure it there — it should reach what the direct path
measured — and then reconsider whether three attempts and the decode are still earning
their place, or whether they have become folklore about a defect that no longer happens.

Also when a schema that cannot say strict starts costing runs. The fix there is to make its
optional fields nullable-and-required rather than to widen the decode, because that keeps
the guarantee at generation time where this ADR argues it belongs.
