# 0001 — Platform worker count

Status: Proposed. Resolves architecture open item 1.

## Decision

Two Platform workers, queue concurrency 4.

## Why

Triage's load is bursty and small: a handful of alerts a day, one F3 tick, one F0
refresh per merge. The expensive work is not the graph — it is the analysis Jobs
and the model calls, both of which happen outside the worker process while the
worker waits on I/O.

Two workers means a rolling restart never drops to zero capacity. Concurrency 4
per worker bounds how many analyses can be in flight at once, which is the real
resource to protect: each one launches a Kubernetes Job.

## Revisit when

Queue depth is non-zero for more than a minute during an incident, or a single
alert storm delays a diagnosis by more than the time it takes a human to start
investigating anyway. Raise concurrency before worker count; the constraint is
cluster capacity for Jobs, not CPU in the worker.
