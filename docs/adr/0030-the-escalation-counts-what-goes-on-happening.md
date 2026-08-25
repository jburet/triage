# 0030 — The escalation counts what goes on happening, not what is new

Status: Accepted, implemented. Corrects
[ADR-0025](0025-code-exceptions-polled-hourly-and-gated-by-volume.md)'s cumulative
escalation, which could not fire as it was built (M8, 2026-08-25).

## Decision

A tick keeps the issues that are **neither new nor regressed** in its window, and uses them
for exactly one thing: to **add their occurrences to the cumulative total of a group some
earlier tick already saw arrive**. Such an issue

- **never creates a group** — a key no row exists for is dropped, not persisted;
- **never clears the floor** — `min_occurrences` applies only to occurrences that arrived new
  or regressed, so refreshing a count is not reporting, and ADR-0025's rule that an issue
  which is neither new nor regressed produces no report still holds;
- **may promote a group through the cumulative escalation only**, which is a statement about
  the group's whole life rather than about this tick.

The group records what the tick was to it: `Novelty.CONTINUING`, alongside `new` and
`regressed`. A group already reported is refreshed the same way, and stays governed by
`errors.reanalyse_after` — the cooldown ADR-0025's amended consequence added.

## Why

**The escalation could not fire, and the ADR said it could.** ADR-0025 pairs the floor with a
cumulative escalation so "a slow bleed is not invisible forever". A group is only ever built
from issues `novelty()` calls new or regressed; **Datadog marks an issue new exactly once**;
so a group held back below the floor was *never observed again*, its `cumulative_occurrences`
never moved, and the escalation was unreachable for the only case it exists for. The one test
that appeared to prove it worked moved every issue's `first_seen` into every tick's window —
it was measuring a defect that goes new hourly, which is not a thing that happens.

**A day of real ticks names the defect it was hiding.** Twenty-four consecutive hourly ticks
on 2026-08-25: `EntityNotFoundException` at `OdbClient.scala:$anonfun$load$6` arrived in one
tick with **4 occurrences**, was held back by the floor of ten, and went on to **186,242
occurrences in the same day** — 1,517 in the very next hour. It never went new again. Under
the gate as built, one of the two loudest defects in the org would have been reported *never*.
Under this decision the next tick's continuing count crossed a hundred and it was reported an
hour after it started.

**The material was already in hand and was being thrown away.** Every tick reads every issue
occurring in its window — 15 in the reference hour, 2 to 51 a tick over the measured day —
and counted them only to say "unchanged". Keeping them costs no extra API call, no model call
and no new endpoint: the search already answers `total_count` per window.

**Counting them cannot double-count.** Error Tracking's `total_count` is the count *for the
window searched*, not a lifetime, so consecutive windows add rather than repeat. The whole of
the error is the poller's five-minute `OVERLAP`, which `merged_error_group` already documents
and already argues is the right direction to be wrong in — it escalates slightly sooner
rather than slightly later.

**Creating a group from a stale issue was the trap to avoid.** Every exception the org has
ever raised goes on occurring. A tick that persisted a row for each of them would build a
table of the whole past, escalate it at a hundred occurrences apiece, and post the backlog as
though it had just happened — which is the one-off sweep M8 deliberately left out of scope
and the error stream [ADR-0023](0023-the-first-release-writes-only-to-slack.md) says to watch
for. Refreshing only what a tick already saw *arrive* is what keeps the front door where
ADR-0025 put it.

**The floor stops being a cliff, which is what makes the number safe to keep.** Because a
held-back group now goes on accumulating, the floor delays a report instead of dropping it.
Replayed over the measured day, every floor from 5 to 200 produces the same five reports and
differs only in the hour each lands in. That is the strongest argument for `min_occurrences:
10`: on this data it is not choosing what to report, only how soon.

## Consequences

- The poller returns a third list — `occurring` — beside `new` and `regressed`, and the tick
  reports `seen_again`: how many known groups had their total moved without being news. A
  tick that re-counted seven groups and reported none has to look different from a tick that
  found nothing.
- `TriageRepository` gains `refresh_error_group`, which returns `None` for a key nothing
  knows. That `None` is the rule above, enforced by the repository rather than by care in the
  node.
- `Novelty` gains a third member that is never an *issue's* novelty. An issue arrives by the
  two doors ADR-0025 named; `continuing` is what a *group* was to a tick.
- A group's `occurrences` for the tick may now under-state its volume in the one case where
  the same defect is both new in one tenant and continuing in another: the floor sees only
  the new half. The cumulative total sees both, so the group is delayed rather than lost, and
  the alternative — summing them — would let a year-old exception clear the floor because one
  more tenant met it.
- The cost is roughly one extra database read per group per tick (2 to 51 issues an hour,
  collapsing to 1 to 7 groups over the measured day). No extra Datadog call, no model call.

## Revisit when

The escalation reports something nobody wanted — a group that trickles at one occurrence an
hour for four days and then posts, when the honest answer was "this is background". The fix
then is `cumulative_occurrences`, a rate rather than a total, or an expiry on a group nothing
has been done about; it is not going back to counting only new issues, which reports nothing
at all.

Also revisit if Datadog ever starts re-marking a long-running issue as new — the assumption
this whole decision rests on is that it does not, measured over one day and stated by the
`first_seen` field's own definition.
