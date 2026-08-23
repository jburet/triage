# 0002 — Confidence levels and per-feature thresholds

Status: Accepted, implemented. Resolves architecture open item 2 and roadmap open point 1.

## Decision

Confidence is a three-level enum — `low`, `medium`, `high` — not a number.

Ticket thresholds, in `config.yaml`:

| Feature | Minimum confidence to file a ticket |
|---|---|
| F1 (incident) | `medium` |
| F3 (database review) | `high` |

## Why

**Three levels, not a percentage.** A model asked for a confidence between 0 and 1
returns 0.73. That reads as a measurement and is a guess. Three levels cannot be
over-read, and the difference between them can be described in the prompt in
terms a reviewer can check.

**F3 is stricter than F1.** They differ in what a false positive costs. F1 fires
when something has already broken and a human is likely already looking; a
marginal ticket is at worst noise next to a real incident. F3 fires every day
against a healthy system, and a stream of medium-confidence database tickets is
exactly how a team learns to ignore the board. The asymmetry is the point.

The gate is a pure function (`triage.nodes.confidence.passes_gate`), not a model
call: the rule that decides whether a team gets interrupted should be readable
and identical on every run.

Confidence is also constrained at the source. `Diagnosis` rejects high confidence
resting on one piece of evidence, and rejects anything above `low` when the cause
is unknown.

## Revisit when

Reviewers validate nearly every F1 ticket unchanged — the threshold is too high
and Triage is dropping useful work into Slack. Or F3 tickets are being closed as
"not actionable" — too low.

Both are answerable from the `evaluations` table without new instrumentation.
