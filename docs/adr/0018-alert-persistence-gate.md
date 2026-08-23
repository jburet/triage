# 0018 — An alert is analysed only once it has persisted

Status: Proposed.

## Decision

An in-scope alert is not analysed when it fires. It is analysed once it has been
continuously in `error` for `thresholds.alert_persistence_minutes`, default **15**,
overridable per team and lowered for `priority: P1`.

The poller ([ADR-0017](0017-alert-ingestion-by-polling.md)) re-checks open cycles on each
tick. A cycle that recovers before the gate is recorded as a `signal` with status
`self_recovered`, carrying its duration, and is never analysed.

**Flapping is counted, not discarded.** `thresholds.flap_count` self-recovered cycles for
the same monitor and group within `thresholds.flap_window_hours` (defaults 5 and 24) is
itself a finding, and goes through the ticket pipeline as an infrastructure diagnosis about
the workload or the monitor — not as an incident.

## Why

The numbers are not close. Across 961 pod-down alert cycles in ten days, **the longest was
nine minutes**. On the StatefulSet replicas monitor over forty days: median cycle five
minutes, 49% under five, 69% under fifteen. Triage's analysis is several model calls and,
for some hypotheses, a Kubernetes Job that clones a repository. Without a gate, the majority
of runs would complete after the condition they describe had already gone, and would spend
real money doing it.

A 15-minute gate discards every one of those 961 cycles, and what it discards is precisely
what a human would not have ticketed either.

**But the flapping is real.** One dev tenant fired every 32 minutes for five minutes at a
time, over and over, for a day. Silently dropping each occurrence would hide a genuine
defect behind the gate that protects us from it, so the count is the second half of this
decision and not an embellishment. The measured example that motivated the whole exercise —
a liveness probe shorter than the pod's own startup, restarting the pod mid-boot — produces
exactly this signature: many short cycles, never one long one. A design that only analysed
long outages would never have found it.

**The honest cost:** a real ten-minute outage is not ticketed. That is accepted. A
ten-minute outage that healed itself is not work a developer picks up days later, and if it
matters it recurs, which is what the flap rule is for.

The gate is also what makes polling viable rather than merely convenient: "still failing
fifteen minutes later" is a question a poller answers for free and a webhook cannot answer
at all.

## Consequences

- The poller carries open cycles between ticks; a signal's lifecycle gains
  `received → waiting → analysing …` and the terminal `self_recovered`.
- `signals` records cycle duration, which is what the flap rule counts and what the
  post-mortem timeline needs anyway.
- Time-to-ticket, the metric in the roadmap's self-evaluation, must be measured from the
  alert firing and not from the gate opening, or it will flatter Triage by fifteen minutes.

## Revisit when

An incident that mattered self-recovered inside the window and nobody was told. The answer
then is a lower threshold for production and high-priority monitors, not removing the gate —
the volume it protects against does not go away.
