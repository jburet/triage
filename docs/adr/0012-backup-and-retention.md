# 0012 — Backup and retention

Status: Proposed. Resolves architecture open item 12 and roadmap open point 2.

## Decision

Nightly `pg_dump` of the whole database, retained 30 days.

| Data | Retained |
|---|---|
| `signals.payload` (raw vendor payloads) | 90 days |
| `signals` rows without the payload | Indefinitely |
| `diagnoses`, `tickets`, `evaluations` | Indefinitely |
| `analysis_results` | 30 days |
| Platform checkpoints | 30 days (Platform TTL) |

## Why

The retention split follows what each table is *for*.

**Raw payloads are debugging material.** They are the largest rows and they stop
being useful once the signal is diagnosed. 90 days covers "why did Triage
misread this alert in the last quarter". Dropping the payload while keeping the
row keeps the signal history intact for counting.

**Diagnoses, tickets and evaluations are the product's memory.** `evaluations`
feeds the self-evaluation the roadmap says is required to justify the investment,
and that metric is only meaningful over quarters. Incident memory — linking a new
ticket to a past similar one — reads `diagnoses`. Both get worse with every row
deleted, and both are small.

**Analysis results are reproducible.** Re-running the Job regenerates them.

**Checkpoints are operational.** A run older than 30 days is not going to be
resumed.

One nightly dump of the whole database rather than per-table policies: Triage's
tables share the Platform's database, and a backup that captures only half of a
consistent state is worse than no backup.

## Revisit when

The database outgrows a nightly full dump — the answer then is WAL archiving with
point-in-time recovery, not shorter retention on the tables that carry the
product's memory.
