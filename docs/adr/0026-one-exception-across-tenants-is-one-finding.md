# 0026 — One exception across tenants is one finding

Status: Proposed. Nothing implements it yet; M8 Phase 2 does.

## Decision

Error Tracking groups an exception **per service**. Triage regroups those issues into one
**error group** per defect, keyed on the exception type, the source location and the
repository the service resolves to. The group names every service it was seen in and the
count in each; one report is written, not one per tenant.

The key is a **rule**, computed from fields Datadog already returned and from
`mapping/resolve.py`'s mono-tenancy rule. **No model call is involved in deciding whether two
issues are the same defect.**

Two issues that look alike but resolve to different repositories stay two groups. A service
that resolves to no repository is its own group, reported and never analysed — there is no
tree to read.

## Why

**The platform is mono-tenant, so Datadog's grouping is grouping by customer.** Every
customer runs its own instance of the same code under its own service name, so one bug in
`OdbClient.scala` is one Error Tracking issue per tenant it happens to. Reporting each of
them is reporting the tenancy, not the bug: six near-identical Slack messages, six
investigations of the same line, and no statement anywhere of the fact that actually matters
— that six customers hit it.

**The ratio was measured, and it is larger than the plan guessed.** On 2026-08-25, over seven
days: **202 issues across 99 distinct services collapse to 35 distinct (type, file, function)
triples** — 5.8 to one. Over thirty days, 320 issues to 69 triples. Inside the single
reference hour, `zeenea.commons.exceptions.EntityNotFoundException` at
`zeenea.repository.orientdb.OdbClient.scala:$anonfun$load$6` appears in six tenants:
`plt-systeme-u-rec`, `plt-systeme-u`, `plt-autostrade`, `plt-pon`, `plt-pon-uat`,
`plt-merck-qa`. Without this decision that is six reports of one line of code.

**The repository has to be part of the key, not just the exception.** A
`NullPointerException` is not a defect; it is a symptom that occurs everywhere. What makes
two issues the same defect is that the same code raised it — so the key is the type *and* the
source location *and* the repository. Two services running different repositories that both
throw `IllegalArgumentException` from a same-named file are not one finding, and collapsing
them would send one team another team's bug.

**A rule, not a model call, because the inputs are already structured.** The exception type,
the file and the function arrive as fields on every issue — 202 of 202 over a week — and the
repository comes from the mono-tenancy rule that already resolves `plt-*` to `datacatalog`.
Asking a model whether two exceptions are "the same" would make the group key
non-deterministic across ticks: the fourth occurrence would not reliably land in the thread
the first one opened, which is the whole point of having a group. This mirrors
[ADR-0019](0019-workload-mapping-from-the-running-image.md) and M6's mapping generally —
what is already structured is joined, never inferred.

**The honest cost: a tenant-specific defect gets flattened.** An exception that only ever
happens for one customer is a fact about that customer's data or configuration, and a group
reporting "seen in six services" reads as a platform bug. The per-service counts are the
mitigation — a group that is 99% one tenant looks different from one spread evenly — and
whether that is enough is unknown until a real group exists. It is not enough to make the
grouping per-tenant again: that trade is six reports to avoid one misreading.

## Consequences

- An `error_groups` table keyed on the group, carrying the per-service counts, the cumulative
  count [ADR-0025](0025-code-exceptions-polled-hourly-and-gated-by-volume.md) escalates on,
  and the Slack thread every message about the group goes into.
- The group, not the issue, is the unit everything downstream sees: the gate counts a group's
  occurrences, the analysis reads one repository, the report names one defect.
- A service the mono-tenancy rule cannot resolve becomes a visible gap rather than silence —
  the same shape F1's "no cartography for it" notice already has.
- Because the key is a rule, a group is stable across ticks without being stored to be found:
  the fourth occurrence computes the same key and finds the same row.

## Revisit when

A group's report is wrong because it merged two defects — the clearest sign being a fix that
closes the group for some services and not others. The answer then is a finer key (the
message shape, or a stack frame) rather than abandoning grouping, because the 5.8-to-one
ratio does not go away.

Also revisit if Zeenea stops being mono-tenant per customer. The key's repository half is
computed by a rule about `plt-*` naming; a shared multi-tenant deployment would make service
name and repository the same thing again and this decision would be about nothing.
