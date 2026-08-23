# 0014 — The analysis entrypoint gathers context, it does not agent

Status: Proposed. Amends the architecture's "Claude Agent SDK in a gVisor Job" line
for the two F0 summarisation kinds.

## Decision

Inside the analysis Job, the entrypoint is a bounded context gather followed by a
single structured `analysis`-tier call: walk the clone, take the files that decide
the answer in priority order until a byte budget is spent, list back everything
left out, ask once, return the validated payload. No agentic loop, no tool use
inside the sandbox.

The selection rules live in `triage.analysis.context` — an ordered set of globs per
profile, a file/byte/tree budget, and a `not_examined` list that reaches the model
with the files themselves.

## Why

The architecture picked the Claude Agent SDK because a repository does not fit in a
context window and something has to choose what to read. That is true, and an agent
is one way to choose. A priority-ordered selection is another, and for *summarising*
a repository it is the better trade:

- **It is testable offline.** The gather is a pure function of a directory tree, so
  the rule that decides what the model sees is pinned by unit tests that cost
  nothing. An agentic loop can only be observed by running it, which needs network
  and spend, which means in practice it is not observed at all.
- **The cost is knowable before the run.** One call with a bounded context is a
  number you can multiply by the repository count. An agent's read-loop is bounded
  only by its own judgement, and F0 re-runs on every merge (ADR-0006).
- **The schema risk disappears.** `StructuredLLM.call` returns a validated Pydantic
  model through tool use. The plan's open risk — "the Agent SDK is assumed to be able
  to emit a JSON document matching the per-kind schema" — was the risk of a free-running
  agent being asked to serialise at the end.
- **A smaller sandbox.** No tool use inside the Job means the gVisor container needs
  GitHub for the clone and the LiteLLM proxy, and nothing else.

What is given up is real: an agent follows references. Asked where a service's
endpoints are, it can read the router, notice it registers a blueprint, and go read
that. The gather cannot — it sees `routes/*` because someone wrote that pattern down.
The mitigation is that a gap is *visible*: files the budget refused are listed to the
model, and an area it could not determine is an `Unknown` with a reason rather than a
plausible invention. A wrong summary is the failure that matters here, because every
later feature believes the map.

This decision covers `summarize_repo` and `summarize_terraform`. The investigative
kinds M3 adds — `code_analysis`, `iac_analysis`, `diff_analysis` — answer a specific
question about a specific commit, which is exactly the shape where following
references pays, and they are free to make the opposite choice.

## Revisit when

Summaries are systematically missing things that live one reference away — endpoints
registered indirectly, dependencies reached through a client library, a framework
configured in a file no pattern names. The evidence is in the `Unknown` reasons and
in the weekly full pass disagreeing with what a human reading the repository would
say. At that point the answer is the Agent SDK for `summarize_repo`, not a longer
list of globs.
