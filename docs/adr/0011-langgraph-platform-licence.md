# 0011 — LangGraph Platform tier, and the fallback

Status: Proposed. Resolves architecture open item 11.

## Decision

Target the self-hosted LangGraph Platform, which needs an Enterprise licence key.
Treat that as a **procurement dependency, not a code dependency**: keep every
Platform-specific surface behind `langgraph.json` and the Platform SDK client in
the ingress, so no graph or node imports it.

If the licence does not arrive: run the same graphs in-process in the FastAPI
service with `langgraph-checkpoint-postgres` for durability and Kubernetes
CronJobs in place of Platform crons.

## Why

This is the only open item with an external blocker, and it is the one most likely
to stall. Designing around it costs almost nothing now and costs a rewrite later.

What the Platform actually provides here is a task queue, cron scheduling, run
history and retries. The fallback replaces each: the queue by the graph's own
concurrency limits, crons by Kubernetes CronJobs, run history and retries by the
Postgres checkpointer. That is a real downgrade in operability — no run browser,
no built-in retry policy — but not in behaviour, and the graphs are byte-identical
either way.

The M1 code already runs on plain `langgraph dev`, so the fallback is not a
contingency plan to be written later; it is the path currently exercised.

## Revisit when

The licence is granted — at which point deployment changes and nothing else does.
That is the property this decision is buying.
