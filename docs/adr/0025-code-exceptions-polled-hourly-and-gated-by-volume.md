# 0025 — Code exceptions are polled hourly and gated by volume

Status: Proposed. The hourly pass and its window exist in code (M8 Phase 1); the gate does
not.

## Decision

F2 reads Datadog Error Tracking on an **hourly** cron, one search per configured track,
and looks only at issues that were **first seen** or **regressed** inside the tick's window.
An issue that is neither produces nothing.

Whether such an issue is then analysed is a question of **volume**, not duration:

- a floor, `errors.min_occurrences` (default **10**), applied to the occurrences in the tick;
- a cumulative escalation, `errors.cumulative_occurrences` (default **100**), so a group
  that never clears the floor is still analysed once it has bled enough in total;
- a per-tick cap, `errors.max_groups_per_tick` (default **5**), with the deferred groups
  named rather than dropped.

The window runs from the watermark minus a five-minute overlap, with a six-hour catch-up
limit; a poller that was down longer replays only the limit and says in the platform channel
what it skipped.

**The environments Triage watches are a filter inside the Datadog query** — `env:prod`,
or `env:(preprod OR prod)` — so an issue from an environment no team configured is never
returned. A deployment where no team declares an environment makes no call at all.

## Why

**Hourly, because there is nothing to wait for.** [ADR-0018](0018-alert-persistence-gate.md)
gates an alert on duration, because an alert fires and recovers and "still failing fifteen
minutes later" is the whole question. An Error Tracking issue has no such shape: it does not
recover, it accumulates. The equivalent question is "how much", and the period the answer is
measured over is the period the pass should run on.

The cadence is also what makes it cheap. Measured on the org on 2026-08-25: **61 new issues
in seven days and 127 in thirty** — about nine a day, so a typical tick sees between none and
one. The reference hour returned fifteen occurring issues and **not one** of them was new or
regressed in it. An hourly pass over a busy org is two API calls and no model call.

**The floor is a guess, and is recorded as one.** [ADR-0018](0018-alert-persistence-gate.md)'s
fifteen minutes came from 961 measured cycles. Nothing equivalent exists for exceptions. What
is measured is the distribution: over the reference hour, occurrences per issue were 6344,
5869, 4009, 850, 835, 650, 435, 200, 29, 15, 4, 2, 2, 2, 1. A floor of ten holds back a third
of them. Over seven days, 45 of 202 issues had fewer than ten occurrences and 99 had fewer
than fifty. The first week's job is to correct these numbers, and the failure mode to watch
for is the one [ADR-0023](0023-the-first-release-writes-only-to-slack.md) names: a floor set
too low turns a team's channel into an error stream, at which point nobody reads it and the
feature has cost more than it gave.

**The cumulative escalation exists because the floor is a cliff.** An exception that happens
four times an hour, every hour, is 96 times a day and never crosses a per-tick floor of ten.
That is a slow bleed and it is invisible without the second number.

**`env:` in the query is a deliberate departure from
[ADR-0017](0017-alert-ingestion-by-polling.md), and that rule stays true where it was
measured.** ADR-0017 forbids reading the environment from an `env:` tag because no *monitor
alert* carries a usable one — for Kubernetes monitors it lives inside `kube_cluster_name`,
and a rule that guesses would call preprod production. APM events are different telemetry
with different tags, and the tag is there: `env:prod` returned all fifteen issues of the
reference hour, `env:preprod` returned none, `-env:prod` returned none. Filtering in the
query rather than dropping afterwards is not an optimisation — it is the difference between
an issue Triage never saw and an issue Triage saw and silently discarded.

The tag is not a promise about deployment, only about the tag: `plt-merck-dev` and
`plt-autostrade-noprod` both answer to `env:prod`. Whether an environment tag means what its
name suggests is the tenant's business; what matters here is that a team's configuration
decides what comes back.

## Consequences

- `config.yaml` gains an `errors:` block; `Feature` gains `F2` and
  `thresholds.ticket_confidence` gains a threshold for it.
- The gate needs a persisted group with a cumulative count, which is the `error_groups` table
  [ADR-0026](0026-one-exception-across-tenants-is-one-finding.md) needs anyway.
- A group persisted with a count and analysed nothing is the common outcome, so the tick has
  to report how many it held back or it looks like a pass that found nothing.
- Because F2 persists a group, it *can* say "this is the fourth time" — which
  [ADR-0023](0023-the-first-release-writes-only-to-slack.md)'s amended consequence says F1
  cannot. If this table proves that store is cheap, F1's recurrence should be revisited
  against it rather than against Jira.
- **The escalation is gated by `errors.reanalyse_after` for a group already reported**, and
  this is measured rather than cautious (M8 Phase 2). The loudest group of the reference hour
  does 10,763 occurrences in it, so against a cumulative threshold of a hundred it crosses the
  next escalation interval on every tick for ever; a rule that only counted would repost the
  same defect hourly, which is the error stream this ADR already says to watch for. The
  escalation says whether there is more to say, the cooldown says when it may be said, and a
  regression bypasses both. The *first* analysis of a group is not gated by time at all — the
  slow bleed still escalates at a hundred with no wait.
- The org's `logs` track answered empty at every window and persona tried. It is asked
  anyway, for eleven bytes an hour, because an empty answer is evidence and a track nobody
  asks about is not.

## Revisit when

A defect that mattered was reported days after it started because it never crossed either
number, or a team asks to be taken off the channel because F2 is noise. Those are the two
directions the same pair of numbers moves in, and only real ticks can say which.

Revisit the hourly cadence if the new-issue rate rises by an order of magnitude — at nine a
day the pass is free, at ninety an hour it is a queue.

Revisit the `env:` filter if Datadog ever stops tagging APM events with an environment, or if
an org appears where the tag is wrong rather than merely surprising. The fix then is the
cluster map, as in ADR-0017 — not a guess.
