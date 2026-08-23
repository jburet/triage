# 0006 — F0 full vs incremental refresh

Status: Proposed. Resolves architecture open item 6.

## Decision

Incremental on every merge to `main`: clone, diff against the last summarised
commit, re-summarise only the touched areas. A full re-summarise weekly, by cron.

What "touched areas" means is defined by
[ADR-0015](0015-incremental-refresh-unit.md), which narrows it: the unit of
invalidation is the whole repository summary, because ADR-0014's entrypoint
produces one document per repository and cannot refresh part of it.

## Why

The roadmap asks for a refresh on every merge. A full re-summarise of every
repository on every merge would make the cartography cost scale with commit
volume rather than with how much the system actually changes — and most merges
touch one package.

Incremental has a known failure mode: summaries drift, because a change in one
module can invalidate a conclusion recorded about another (a moved entry point, a
renamed dependency). The weekly full pass is the correction for that, and it runs
when nobody is waiting.

The system map is used by every other feature to locate code, infra and owners.
It being quietly stale is worse than it being briefly out of date, which is why
the full pass is scheduled rather than discretionary.

## Revisit when

The weekly full pass regularly produces a materially different map from the
incremental one. That means the diff heuristic is missing dependencies, and the
answer is a better invalidation rule, not a more frequent full pass.
