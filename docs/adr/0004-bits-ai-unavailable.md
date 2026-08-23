# 0004 — Behaviour when Datadog Bits AI is unavailable

Status: Proposed. Resolves architecture open item 4.

## Decision

Degrade, never wait. One retry after 60 seconds; then collect alone, add an
explicit entry to `unknowns`, and cap the diagnosis at `medium` confidence.

## Why

The architecture treats Bits AI as the primary telemetry input, with Triage adding
the code and IaC layer on top. Blocking on it would mean an outage in a vendor's
investigation feature becomes an outage in Triage — during an incident, which is
the moment it is least affordable.

One retry covers a transient failure. Beyond that, a partial diagnosis delivered
in minutes beats a complete one delivered after the incident is over.

The confidence cap and the `unknowns` entry are what make degradation honest. A
diagnosis assembled without the telemetry correlation Bits AI provides is
genuinely less certain, and the ticket must say so — otherwise a reviewer cannot
tell a degraded run from a normal one. Under ADR-0002 the cap still allows an F1
ticket, but no longer an F3 one.

## Revisit when

Bits AI outages are frequent enough that degraded diagnoses become the common
case. At that point the collectors Triage runs itself deserve to be first-class
rather than gap-filling.
