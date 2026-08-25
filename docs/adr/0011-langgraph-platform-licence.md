# 0011 — LangGraph Platform tier, and the fallback

Status: Accepted **on the fallback**, 2026-08-25. There is no Enterprise licence for an
on-prem deployment, so the branch this ADR wrote is the one taken. Resolves architecture
open item 11.

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

## What the fallback actually costs, now that it is the design

The three replacements above are not equal, and two of them were understated.

- **The queue is the one that bites.** With no Platform, `_launch` *awaits*
  `run_incident` inside the poller tick, one signal at a time: `create_run`
  returned as soon as a run was queued, and an in-process launch returns when the
  incident is finished — measured at 64s. A tick that opens the gate stops polling
  for as long as the analysis takes, and two signals in one tick run serially. "The
  graph's own concurrency limits" was not a replacement for a queue, because
  nothing here limits concurrency; it runs everything to completion in the caller.
  The poller needs to launch incidents as supervised tasks with a cap, and that is
  new code rather than configuration.
- **`langgraph-checkpoint-postgres` is a declared dependency and is wired
  nowhere.** Durability is therefore currently zero: a process that dies mid-incident
  leaves a signal in `analysing` that nothing resumes and nothing reclaims. The
  Platform would also have retried. Until the checkpointer is real, a restart is a
  lost incident and a stuck row.
- **Triage has no runtime image.** M7 built the *analysis* image; the poller has
  only ever run from a checkout. Whatever schedules it — CronJob or Deployment —
  needs an image that does not exist, and the same registry that blocks the
  analysis image blocks this one.

`deploy/platform/cron-alert-poller.yaml`, `scripts/apply_cron.py` and the cron
methods on `PlatformRestClient` are unreachable under this decision. They are kept,
not deleted, because that is the property this ADR bought: if a licence ever
arrives, deployment changes and nothing else does.

## Revisit when

A licence becomes available — at which point deployment changes and nothing else
does. That is the property this decision was buying, and taking the fallback is
what proves it was worth buying: no graph, node or schema changes today.

Revisit sooner if the poller's own supervision turns out to be the thing that
breaks — a stuck `analysing` row nobody reclaims, or ticks skipped while an
incident runs. That is the queue being missed, and it is an argument for a real
work queue rather than for the Platform specifically.
