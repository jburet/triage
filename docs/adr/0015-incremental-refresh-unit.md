# 0015 — The unit of incremental refresh is the whole repository summary

Status: Proposed. Makes ADR-0006's undefined "touched areas" concrete, and narrows it.

## Decision

On a merge, compare the merged commit against the commit the map already records
and judge the changed paths:

- **A path matters exactly when the gather would read it.** `context.reads` is the
  single definition, so the rule cannot drift from what the summariser actually
  opens. Everything else — tests, changelogs, lockfiles, anything under an excluded
  directory, Terraform state — is inert.
- **Any path that matters re-summarises the whole repository.** Not the touched
  area: the whole thing.
- **No path that matters leaves the summary standing.** Its rows keep their payload
  and only their `source_commit` moves forward.

Every uncertainty resolves towards re-summarising: no prior summary, no recorded
commit, a comparison GitHub will not answer, or a comparison that hit GitHub's
300-file cap.

The comparison is a read-only GitHub API call made by the graph, not a clone.
Architecture §7 gives GitHub egress to the analysis Job precisely so repository
*content* is only ever read inside the sandbox; a list of filenames is not content,
and cloning from the graph pod would put the thing the sandbox exists to contain
into the graph's network policy.

## Why

ADR-0006 says "re-summarise only the touched areas" and leaves the area undefined.
The plan's own assumption was "files changed → their top-level package". Both
presume a summary can be refreshed in pieces, and under ADR-0014 it cannot: the
entrypoint produces one whole `RepoSummary` from one call. A partial re-summarise
would have to either merge a thin new document into the stored one — which silently
keeps endpoints that were deleted, because a document about `src/payments` cannot
say that `src/ledger`'s endpoint is gone — or replace the stored one with the thin
document, losing everything it did not look at. The first is a map that is
confidently wrong; the second is a map that is visibly empty. Neither is worth the
tier call it saves.

So the saving is taken where it is real and costs nothing to be sure of: the merge
that *cannot* have changed the summary. A changed test file, a changelog entry, a
dependency lockfile. The rule is exact rather than heuristic, because it asks the
gather itself, and being exact is what makes skipping safe.

The asymmetry is the whole argument. An unnecessary re-summarise costs one
`analysis`-tier call. A wrongly skipped one leaves every later feature — F1's
location, F3's owning team — reading a map that is wrong until the weekly full pass
notices. The rule fails towards spending money.

Areas are still computed and recorded on the decision, because the operator reading
a run wants to know what moved, and because they are what a future partial refresh
would key on.

## What this does not see

- A change in one area invalidating a conclusion about another: a moved entry point,
  a renamed dependency. ADR-0006 already names this as incremental's failure mode and
  the weekly full pass as its correction.
- A purely additive change in an unread area. The file tree travels to the model
  alongside the files, so a new directory can shift a summary even when nothing
  readable changed.
- Whatever the previous summary got wrong. Carrying it forward carries its mistakes.

## Revisit when

Merges that touch a readable file become both frequent and expensive enough that the
skip rate stops paying — a large monorepo where every merge lands in `src/`. The
answer then is not a cleverer diff: it is an entrypoint that can be *shown* the
previous summary and asked to revise it, at which point a partial refresh stops
meaning a thinner document. ADR-0006's own instruction applies — fix the
invalidation rule, not the cadence.
