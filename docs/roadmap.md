# Triage — Product Roadmap (v5)

## Mission

Turn any production alert or signal into a **ticket that a developer can act on without further investigation.**

The product is not "an AI SRE". It is the layer between observability (Datadog) and the development team: it reads the code, the infrastructure-as-code and the database, and produces a precise, evidence-backed, validated work item.

## Context

- Internal tool. Datadog and Grafana already in place; Triage integrates with Datadog only.
- SRE team delegates all analysis to developers; developers bear the investigation cost.
- Goal: remove the investigation burden from developers, and make the SRE team owner of the agent.

## Guiding principles

- **Read-only on production systems**: cluster, databases, observability stack.
- **Writes only to collaboration tools**: Slack and Jira. Triage never writes to Git.
- **No direct action on infrastructure** — ever.
- **Production scope only.**
- **Analysis only**: Triage diagnoses and specifies; it never produces code or PRs. Fixing is the developer's job.
- **Never invent**: any field that cannot be filled with confidence is explicitly marked "unknown".
- **Buy what observability vendors do well** (detection, instrumentation, retention). **Build what they cannot**: correlation across a specific system, and code, Terraform and database understanding ([ADR-0016](adr/0016-datadog-collected-by-triage.md)).

---

## The core pipeline

All features feed the same pipeline:

```
Signal  →  Collect  →  Diagnose (telemetry + code + IaC + DB)  →  Ticket  →  Human validation  →  Fix and close (developer)
```

### The "developable ticket" specification

A ticket is complete only if a developer could start working on it without asking a question. Required content:

1. **Symptom**: what was observed, with numbers and time window.
2. **Impact**: users, services, SLOs affected.
3. **Probable cause** with confidence level.
4. **Evidence**: links to metrics, logs, traces, Kubernetes events.
5. **Location**: repository, deployed commit, suspected files/functions, or Terraform module/resource.
6. **Expected change**, expressed as a verifiable acceptance criterion the developer checks before closing (e.g. "p95 of `/orders` back under 300 ms", "no OOM restarts over 24 h").
7. **Out of scope**: what the fix must not touch.
8. **Hypotheses ruled out**, so nobody redoes the work.
9. **Unknowns**, stated explicitly.

### Jira workflow — postponed

The first release writes **only to Slack** ([ADR-0023](adr/0023-the-first-release-writes-only-to-slack.md)):
one threaded report per incident in the owning team's channel, containing everything below.
A ticket asks a human to validate before the work enters a backlog, and nobody has reason to
trust the agent yet; a message in the channel the team already watches asks for nothing. The
Jira path stays in the tree, configurable and tested, for when someone asks to keep a report.

When it returns:

- `Proposed by agent` → created automatically, routed to the owning team's board.
- `Validated` → set by a human (lead dev or SRE); the ticket enters the team's backlog.
- `In progress` / `Done` → standard; the developer closes the ticket after checking the acceptance criterion.

---

## F0 — System cartography (foundation)

- Configuration: application and IaC repositories, owning teams, Slack channels, clusters, databases.
- Initial analysis → structured summary per repo: languages, frameworks, entry points, endpoints, inter-service dependencies, database access patterns, observability setup.
- Terraform analysis, **code only** (no state comparison): resources, sizing, networking, managed databases, mapping modules ↔ services.
- Output: a system map used by every other feature to locate code, infra and owners.
- Refreshed on every merge to `main`.
- **Workload mapping** (M6): the map is keyed on the name a repository deploys as, which no
  per-tenant instance of the mono-tenant `platform` ever matches. The running workload's own
  container image resolves the repository, the architecture document seeds which IaC
  repository provisions it, and naming patterns are the fallback rather than the rule.

## F1 — Incident to ticket

- Trigger: alert (unavailability, crash/restart, OOM, latency…), once it has been firing for 15 minutes ([ADR-0018](adr/0018-alert-persistence-gate.md)).
- Collects from Datadog itself over an adaptive window — the monitor's own metric, events at service and namespace scope, aggregated logs, traces where the service is instrumented — as a fixed sweep followed by a bounded follow-up loop.
- Adds what Datadog cannot: analysis of the code at the deployed commit, Terraform analysis, diff vs previous version.
- Outputs: immediate Slack notice, then a ticket via the core pipeline.
- Generates the post-mortem draft from the ticket and timeline.

## F2 — A recurring code exception becomes a report

A second input beside F1, built as M8 (2026-08-25). Where F1 waits for a monitor to fire,
F2 asks Datadog Error Tracking every hour which **code exceptions** are new or have
regressed, and reports the ones that are loud enough to be worth a developer's hour.

- Trigger: an Error Tracking issue first seen — or regressed — inside the tick's window, on a
  service in a watched environment, once it clears a volume gate ([ADR-0025](adr/0025-code-exceptions-polled-hourly-and-gated-by-volume.md)).
  The gate is occurrences rather than duration: an error issue does not recover, it
  accumulates, so a per-tick floor is paired with a cumulative escalation that makes a slow
  bleed visible and a cooldown that stops a loud one being reposted hourly.
- The same exception in several tenants of the mono-tenant platform is **one** finding, keyed
  on the repository the tenancy rule resolves ([ADR-0026](adr/0026-one-exception-across-tenants-is-one-finding.md)).
  The per-tenant counts survive, because a defect that is 99% one customer is a fact about
  that customer.
- The issue names the exception type, the message, the file and the function, and the
  versions it was first and last seen on — so F2 does not have to infer where to look. The
  file and function are converted into repository paths and read ahead of the analysis's own
  globs ([ADR-0028](adr/0028-a-class-name-is-not-a-path.md)); the version, where a repository
  claims one, is the commit the code is read at.
- Outputs: one threaded Slack report per group, in the owning team's channel, carrying the
  nine ticket-spec sections plus the exception's own identity. Every later report about the
  same group replies in the same thread, across ticks.
- Evidence, when the sampler kept some: the occurrences are found by `service:<svc>
  status:error` over raw spans and matched on the exception type inside the OpenTelemetry
  span events, and the report then carries a real stack trace and the frames it names
  ([ADR-0029](adr/0029-the-exception-is-in-the-otel-span-events.md)). The org's error-span
  retention filter is still disabled, so for many groups nothing retained is this defect —
  F2 says which kind of absence that is, names the filter, and reports anyway.

## F3 — Daily database review — postponed

Not in the first release (ADR-0023); the plan of 2026-08-23 stands unstarted.

- Top queries, response times, locks, vacuum/bloat, index usage, connections, storage growth.
- Traces slow or costly queries back to the calling code and owning team via F0.
- Output: one global daily report (changes + open items); each significant recommendation becomes a ticket via the core pipeline. DB config changes → ticket pointing at the Terraform resource to change.


---

## Cross-cutting features

- **Alert coverage audit**: detect missing, noisy or obsolete alerts and SLOs; an agent cannot convert an alert that was never configured. *Measured 2026-08-23: 6,489 monitor-alert events in 7 days, 4,232 of them `error`, from 19 monitors — of which the production pod-down signal is 28. Three Synthetics monitors fired 1,344 times with zero recoveries. On these numbers F1 would spend most of its budget on monitors that are themselves the defect, which argues for moving this ahead of F1 rather than after it.*
- **Change correlation beyond deployments**: feature flags, cloud config changes, DB migrations, vendor incidents.
- **Incident memory**: link new tickets to past similar ones.
- **Deduplication**: update existing tickets rather than creating duplicates.
- **Agent self-evaluation**: root-cause accuracy, first-time-right ticket rate, time-to-ticket, false positives — fed by reviewer feedback at validation and at closure. Without this, the investment cannot be justified.
- **Cost recommendations** (later): rightsizing proposals as tickets pointing at Terraform resources, reusing F0 data.

---

## Delivery order and rationale

1. **Ticket specification + Jira workflow** — the product definition; cheap, unblocks everything.
2. **F0** — foundation.
3. **F1** — self-collected telemetry plus the code/IaC layer.
4. **F2** — a second input, cheap because Error Tracking already names the code. Built after
   F1 and before F3, because it reuses F1's whole back half — the Analysis sub-graph, the
   ticket pipeline, the service map — and adds only its own front end.
5. **F3** — independent daily job.
6. Cross-cutting: alert coverage audit, self-evaluation.

Removed from scope: weak-signal detection (delegated to Datadog anomaly detection), post-release monitoring and automated ticket verification.

## Before starting

- Measure today's average developer time per escalation. This is the metric the agent must move.
- Make the SRE team the owner of the agent: F0 configuration, ticket validation, infra tickets, F3 reports.

## Open points — resolved

| Point | Decision |
|---|---|
| Initial confidence threshold values | Three-level confidence; F1 ≥ `medium`, F3 ≥ `high` — [ADR-0002](adr/0002-confidence-thresholds.md) |
| Retention of incident memory | `diagnoses`, `tickets` and `evaluations` kept indefinitely; raw payloads 90 d — [ADR-0012](adr/0012-backup-and-retention.md) |
| Where the post-mortem draft is published | Jira comment on the incident ticket, linked from Slack — [ADR-0010](adr/0010-postmortem-destination.md) |

The two items under **Before starting** remain open, and are not decisions Triage
can take for itself: measuring today's developer time per escalation, and making
the SRE team the agent's owner.
