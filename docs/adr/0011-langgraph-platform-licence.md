# 0011 — LangGraph Platform tier, and the fallback

Status: Accepted **on the Platform**, 2026-08-25, in the **self-hosted Hybrid** flavour:
Zeenea's own control plane at `langsmith-dev.infra.zeenea.app`, data planes in Zeenea's EKS.
Resolves architecture open item 11.

Briefly recorded as "fallback taken" earlier the same day, on the reading that no licence
meant no Platform. The licence question is settled by fact rather than by procurement: that
control plane is already standing and already serves two agents. Triage joins it.

## Decision

Target the LangGraph Platform in the **Hybrid** flavour — LangSmith's managed control
plane, a data plane in Zeenea's AWS — and keep every Platform-specific surface behind
`langgraph.json` and one client, so no graph or node imports it.

Two agents already deploy this way, and between them they answer every mechanical question.
`data-intelligence-assistant`'s `scripts/deploy-ecr.sh` runs `langgraph build` and pushes to
ECR in **eu-west-3** for **linux/arm64** — the data plane runs on Graviton — then the image
path goes into *Create Deployment*, and `scripts/register_crons.py` registers schedules
through `langgraph-sdk`'s `crons.search` / `crons.create`. `agent-classification` does the
same deployment **as Terraform**: a `langsmith_deployment` resource on the `zeenea/langsmith`
provider pointed at the control plane, with its scale and resource spec declared. That is the
better path for Triage — a reviewable resource rather than a console field.

Triage gets its **own deployment**, not a share of another. Nothing leaves the perimeter:
the control plane is Zeenea's, the data plane is Zeenea's EKS (`langgraph-dataplane-*`), and
LiteLLM is reached at in-cluster service DNS, as DIA reaches it at
`http://litellm-proxy.litellm-proxy:4000/v1`.

Treat the *fully* self-hosted flavour as a procurement dependency, not a code dependency.
The fallback below stands as the documented answer if the hybrid one is ever withdrawn.

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

## What the fallback would cost, which is why the Platform is worth having

Measured while the fallback was briefly assumed. The three replacements are not equal, and
two of them were understated — which is the argument for the Platform rather than against it.

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

`deploy/platform/cron-alert-poller.yaml`, `scripts/apply_cron.py` and the cron methods on
`PlatformRestClient` are the live path under this decision, not a contingency: they do for
Triage what `register_crons.py` does for DIA.

## Revisit when

The hybrid flavour stops being available, or the data plane may no longer run in Zeenea's
AWS — at which point the fallback above is the design and its three costs become work. The
half-day this ADR spent on the fallback and back is the evidence that the separation holds:
no graph, node or schema changed in either direction.
