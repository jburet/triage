# 0017 — Alerts arrive by polling the Datadog event stream

Status: Proposed. Supersedes the *Signal ingestion* row of the decisions table and the
Datadog webhook path in [architecture §1](../architecture.md#1-high-level-view) and
[§8](../architecture.md#8-deployment).

## Decision

A Platform cron polls `POST /api/v2/events/search` every 60 seconds for monitor alert
events. There is no Datadog webhook and no Datadog ingress endpoint.

- **Watermark with overlap.** Query from `last_watermark − 2 min`; deduplicate on
  `signals.external_id`, the Datadog event id. At-least-once plus idempotent, rather than
  a cursor that has to be exact against ingestion lag.
- **One signal per alert cycle**, keyed on `@monitor.id` plus the firing group. The event
  carries `monitor.alert_cycle_key_txt` for this. Re-notifications do not create signals.
- **Bounded catch-up.** After an outage the poller replays at most 30 minutes and posts one
  Slack line naming what it skipped. An alert three hours old is not worth a ticket; being
  silent about it is worse than saying so.
- **Transitions**: `error` and no-data in, `success`/`ok` recorded against the open cycle as
  its recovery time and not otherwise acted on.

**Scope is matched by pattern, never by enumeration.** `config.yaml` gains, per team,
glob patterns over service and namespace, an `environments` list, and a cluster-name →
environment map. Resolution ladder: the `service:` tag → a tenant pattern → 
`kube_namespace` / `kube_stateful_set` → out of scope.

**Two different out-of-scope outcomes.** An alert that resolves to no configured team is
recorded as a `signal` with status `out_of_scope` and produces no Slack message. An alert
that resolves to a configured team but a service unknown to the system map keeps the Slack
notice to that team's channel and is not analysed.

Reading the alert from Slack is deferred, and only for the ability to reply in the Datadog
message's own thread. `SlackClient` takes a `thread_ts: str | None` from the start so that
change is not a signature change through every notify path.

## Why

**Enumeration is impossible here.** One StatefulSet monitor fired for 66 distinct groups in
40 days, and the groups are per-customer tenants — `plt-merck`, `plt-hcl-software-uat`,
`plt-telmai`. A `service:` value is a tenant instance, not a service in the F0 sense. A list
of services in `config.yaml` would be obsolete the day a customer is provisioned.

**No alert carries a usable `env:` tag.** In the measured sample the environment is inside
`kube_cluster_name:preprod-euw3` for Kubernetes monitors and inside the monitor's *name*
("… in prod") for others. Production-only cannot be a tag match, so the cluster map is not
a convenience but the only thing that works.

**Polling expresses the persistence gate; a webhook cannot.** [ADR-0018](0018-alert-persistence-gate.md)
requires knowing that an alert is *still* firing N minutes later. That is the natural shape
of a poller and an awkward bolt-on to a push endpoint.

**The alternatives cost more and give less.** A webhook means `@webhook-triage` in every
monitor message — Datadog's notification rules route to email, Slack, PagerDuty and Teams,
not to webhook handles, so there is no central way to do it. Datadog does not sign webhooks,
so it also means a shared-secret header and a public endpoint. Reading the Slack message
means a bot with `channels:history` over the alerting channels and parsing a rendering meant
for humans; the two alerts that started this work differ enormously in how much they say,
and the poorer of the two would have given Triage almost nothing.

**It deletes a component.** With GitHub and Jira webhooks still ahead of us the ingress will
exist eventually, but F1 no longer needs it, no longer needs an inbound path into the
cluster, and no longer needs a webhook authentication story. F1 becomes a cron like F3.

60 seconds of latency is nothing against an analysis measured in minutes.

## Consequences

- M3 Phase 4 becomes a poller node and a cron entry rather than a FastAPI endpoint;
  behaviours 4.1 and 4.2 are rewritten, 4.3 keeps its Platform-versus-in-process split,
  4.4 splits in two as above.
- `signals` gains `out_of_scope` and `self_recovered` to its status set, and stores the
  Datadog event id, the monitor id, the group and the recovery time.
- A new table or a single row holds the poller watermark.
- `config.yaml` gains the patterns, the environment list and the cluster map.

## Amended 2026-08-23, after the first live run

**The environment may also be read from the monitor's own query.** The rule as
written resolved the environment only from `kube_cluster_name`, and the first
live run showed what that costs: `Zeenea service or platform pod down in prod`
groups `by service` alone, carries no cluster tag at all, and therefore resolved
to no environment — so every alert from the monitor this feature exists for would
have been recorded `out_of_scope` and dropped. Its query filters `env:prod`, and
that is a declaration by whoever wrote the monitor, not an inference: it is read
when there is no cluster, and named as the source in the routing reason.

The monitor's *name* is still not read, though this ADR mentions it. "prod-like
preprod" contains "prod", and a rule that cannot tell those apart is exactly the
guess the cluster map exists to avoid.

## Revisit when

Someone needs Triage to react inside a minute, or the in-scope alert volume grows enough
that a 60-second page of results is routinely truncated. The first argues for the webhook
on a handful of monitors alongside the poller; the second argues for a shorter interval
before it argues for anything else.
