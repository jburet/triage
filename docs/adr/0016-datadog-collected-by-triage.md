# 0016 — Triage collects Datadog telemetry itself, over REST

Status: Proposed. Supersedes [ADR-0004](0004-bits-ai-unavailable.md) and the Datadog
row of the tools table in [architecture §5](../architecture.md#5-tools-layer).

## Decision

Datadog Bits AI is not an input. Triage collects the telemetry itself, through
Datadog's REST API, and does the correlation in `qualify`.

**Access.** A scoped, read-only application key created on a **service account** —
application keys are user-scoped and die with the user who owns them. Scopes:
`monitors_read`, `timeseries_query`, `logs_read_data`, `apm_read`, `events_read`.
`TRIAGE_DATADOG_SITE` (the API host, `api.datadoghq.eu` here), `TRIAGE_DATADOG_API_KEY`,
`TRIAGE_DATADOG_APP_KEY`. REST rather than the Datadog MCP server, for the reasons in
[ADR-0013](0013-jira-over-rest.md) plus one specific to collection: the queries must be
deterministic and their volume bounded, because their output is spent as prompt tokens.

**Shape: a fixed sweep, then a bounded follow-up loop.** The sweep is deterministic and
parallel — the monitor's own metric over a widened window, events at *both* service and
namespace scope, logs aggregated then sampled newest-first anchored at the alert, and a
span-presence check. The follow-up loop lets the `analysis` tier request up to
`collection.max_followup_calls` (default 6) further calls, chosen from the same collector
set. Sweep for breadth, loop for depth.

**The alert event already carries the monitor.** A monitor alert event includes
`monitor.query`, `monitor.options` (thresholds, `new_group_delay`, `renotify_interval`),
`monitor.priority`, `monitor.groups` and `monitor.result.logs_url`. `GET /api/v1/monitor/{id}`
is a follow-up call, not a sweep call.

**Kubernetes change events are diffed, never read by title.** `prev_value` and `new_value`
on a `kubernetes` change event give a full before/after object spec. The diff is the fact;
the title is not.

**Reduction happens before the model, not in the prompt.** Logs are deduplicated by message
template and stripped to timestamp, status and message; events below `warn` are dropped
unless their title is a lifecycle one; timeseries are downsampled. Caps live in
`config.yaml` under `collection:`.

**An empty collector is disambiguated by widening, not assumed.** Before recording that a
signal was missing, the same query is re-run namespace-wide over seven days. Empty there
means not instrumented — an `unknowns` entry. Empty only in the incident window is
evidence, and is offered to `qualify` as such.

ADR-0004's retry-and-degrade branch is deleted along with its confidence cap. Confidence is
capped only by what the collectors actually returned.

## Why

This was settled by running it by hand against a real alert
(`plt-hcl-software-uat`, 2026-08-22 00:43–00:49 UTC, captured in
`tests/fixtures/datadog/hcl_software_uat_20260822/`) rather than by argument.

**It reaches root cause.** Six calls and about three seconds produced the full chain:
container start, `Liveness probe failed: connection refused`, `Killing: Container platform
failed liveness probe, will be restarted`, exit code 1, then a four-minute startup sequence
(freeze db, EBS snapshot) that the probe is shorter than. That is a developable ticket
against a Helm chart, and nothing in it required Bits AI or a repository clone.

**The sweep alone would have got it wrong.** The events window shows
`StatefulSet plt-hcl-software-uat deployed`, which reads as a deployment causing a restart.
Diffing `prev_value` against `new_value` shows every field identical — same image, same
digest, same replica counts — with only `ready_replicas` changing 1→0. Datadog emits
"deployed" for any StatefulSet object update, readiness included. A collector that trusted
the title would have produced a confident, wrong diagnosis; one that diffs kills the
hypothesis in one call. This is the single strongest argument for the follow-up loop: the
diff was worth making only *because* the sweep surfaced the event.

**Service scope truncates the story.** The container exit codes and every probe failure were
visible at `kube_namespace:` scope and absent at `service:` scope. Both are in the sweep for
that reason.

**Raw payloads do not fit.** Sixty log entries came back as 176 KB — roughly 45k tokens for
one collector, against a 500k per-run budget ([ADR-0007](0007-model-tiers-and-budgets.md))
that also has to cover analysis and composition. Twenty-five of those sixty lines were the
same `platform api authentication failed`. Reduction is the design, not an optimisation.

**Emptiness is genuinely ambiguous.** The span search returned nothing for the incident
window, which during an outage reads as "the service is down". Re-run namespace-wide over
seven days it also returned nothing: the tenant is not instrumented at all. Same response,
opposite meaning, and only the wider query separates them.

Bits AI was worth buying for correlation. What we lose is real, and what we gain is that
every fact in a ticket comes from a call we made and can show, and that a vendor feature
being unavailable during an incident is no longer an outage in Triage.

## Consequences

- `DatadogClient` loses `bits_ai_investigation` and gains the collector set. `FakeDatadogClient`
  replays the captured fixtures.
- The F1 graph loses `fetch_bits_ai` and its retry edge; `collect_gaps` becomes `collect`.
- `config.yaml` gains a `collection:` block for the caps and the follow-up budget.
- The roadmap's "Before starting" item *enable Datadog Bits AI SRE* is dropped.

## Revisit when

The follow-up budget is regularly exhausted without the diagnosis converging — that means
the sweep is missing a collector, and the fix is to promote it, not to raise the budget.
Or Bits AI becomes available and demonstrably finds causes this collection misses, on
incidents we have already diagnosed by hand; the fixtures above are the benchmark to judge
that against.
