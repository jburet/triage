# 0025 — Code exceptions are polled hourly and gated by volume

Status: Proposed, and its numbers are no longer guesses. The whole pass exists in code
(M8 Phases 1-4). The floor, the escalation and the cap were corrected against 24 consecutive
hourly ticks run live on 2026-08-25 — the revisit condition below, met in the first day — and
the escalation itself was found unable to fire and repaired by
[ADR-0030](0030-the-escalation-counts-what-goes-on-happening.md).

## Decision

F2 reads Datadog Error Tracking on an **hourly** cron, one search per configured track,
and looks only at issues that were **first seen** or **regressed** inside the tick's window.
An issue that is neither **produces no report** — it is counted into the total of a group
already known and does nothing else, which is
[ADR-0030](0030-the-escalation-counts-what-goes-on-happening.md) and the only thing that makes
the escalation below able to fire.

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

The average holds and the distribution is lumpy: over the 24 measured ticks, one hour brought
90 new-or-regressed issues and eighteen brought none. The cadence is not what absorbs that —
the per-tick cap is.

**The floor was a guess, and the guess was right for the wrong reason.** It was set from the
occurrences *per issue* of every issue *occurring* in one reference hour — 6344, 5869, 4009,
850, 835, 650, 435, 200, 29, 15, 4, 2, 2, 2, 1 — when the gate applies to occurrences *per
group* of issues that were *new or regressed*. That is a different and much quieter
population, and reasoning from the loud one would tune the number wrongly.

**What a day of real ticks says** (2026-08-25, 24 consecutive hourly ticks, the pipeline's
own functions, no fixture). Eleven groups arrived. Their occurrences in the tick each arrived
in: **1, 1, 1, 2, 3, 4, 5, 30, 189, 7758, 37691**. **Eighteen of the 24 ticks brought no group
at all.** The busiest brought five — a wave of some ninety new-or-regressed issues in one
hour, one defect arriving across dozens of tenants at once — and that is the tick the cap of
five is sized for; nothing else came near it. Nothing in the day lands between 6 and 29, so
every floor in that range decides it identically. Ten takes four groups up on arrival and
holds seven back.

The floor stays at ten, and the sensitivity is why it can. With the escalation repaired
(ADR-0030) a held-back group goes on accumulating, so the floor **delays** a report instead of
dropping it: replayed over the same day, **every floor from 5 to 200 produces the same five
reports** and differs only in the hour each lands in. A floor of **1** produces seven, five of
them inside the single wave tick — which is exactly the failure mode
[ADR-0023](0023-the-first-release-writes-only-to-slack.md) names, a team's channel turned into
an error stream that nobody reads. Ten is the middle of the range that behaves identically,
not a value the data singled out.

This is one day, and an unusual one: it contained that wave. A quieter day is what its first
ten ticks were — eight hours with nothing new at all, two or three known groups re-counted per
tick, and no report posted.

**The cumulative escalation exists because the floor would otherwise be a cliff.** An
exception that happens four times an hour, every hour, is 96 times a day and never crosses a
per-tick floor of ten. That is a slow bleed and it is invisible without the second number.

As first built the second number could never move: a group is only ever derived from issues
that are new or regressed, Datadog marks an issue new exactly once, so a group held back below
the floor was never observed again. The measured day names what that cost — one group arrived
with **4** occurrences, was held back, and did **186,242** occurrences over the same day
without ever being new again. [ADR-0030](0030-the-escalation-counts-what-goes-on-happening.md)
feeds the total from the issues that go on occurring, which is the only material there is; it
also keeps this ADR's rule that such an issue produces no report of its own.

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
- **The escalation needed a source of counts, and it is the issues this ADR looks past**
  (ADR-0030). A tick keeps every occurring issue and adds it to the total of a group it
  already knows; it may not create a group from one, and it may not clear the floor with one.
  So the front door stays where this ADR put it — new or regressed, or no report — and the
  cumulative total behind it is now a number that actually moves.
- **What a corrected day costs a channel: five reports in 24 hours, at most two in any tick.**
  Four arrived by the floor and one by the escalation, and 20 of the 24 ticks posted nothing.
  That is the number to hold the "F2 is noise" complaint against.

## Revisit when

A defect that mattered was reported days after it started because it never crossed either
number, or a team asks to be taken off the channel because F2 is noise. Those are the two
directions the same pair of numbers moves in, and only real ticks can say which.

The first day of real ticks arrived on 2026-08-25 and moved the numbers not at all — it moved
their justification, and it found the escalation unable to fire. The next revisit wants
**several** days, and the number to watch is the one this day could not produce: how often a
group is reported by the escalation rather than by the floor. One in five here; if that
becomes most of them, the floor is doing nothing and the escalation is the whole gate.

Revisit the hourly cadence if the new-issue rate rises by an order of magnitude — at nine a
day the pass is free, at ninety an hour it is a queue.

Revisit the `env:` filter if Datadog ever stops tagging APM events with an environment, or if
an org appears where the tag is wrong rather than merely surprising. The fix then is the
cluster map, as in ADR-0017 — not a guess.
