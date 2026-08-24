# 0020 — A commit nothing observed is never presented as the deployed one

Status: Proposed. The commit half of [ADR-0019](0019-workload-mapping-from-the-running-image.md)'s ladder.

## Decision

The commit an analysis reads is resolved in a stated order, and **what resolved it
travels with it** as a `CommitSource`:

1. **`image_tag`** — the tag is the commit outright.
2. **`github_tag`** — the tag is a build number, and GitHub says which commit that tag
   names. An annotated tag resolves to the commit it points at, never to the tag
   object's own SHA. Only the spelling `config.yaml` declares is looked up; no second
   tag spelling is tried on a hunch, because a guessed tag points somewhere specific
   and wrong.
3. **`default_branch`** — no build was identifiable, so the branch is read *as it stood
   when the incident fired*, not as it stands now.

A repository `config.yaml` does not declare gets no GitHub read at all, and its
`Unknown` says that rather than blaming the tag.

Then the rule this ADR exists for. **A `default_branch` commit may not carry `high`
confidence, and the diagnosis states in its `confidence_rationale` that the analysis
read the default branch as it stood because no build was identifiable.** The same cap
and the same shape of sentence apply to a repository a name pattern picked out: there
the commit is the repository's last summarised one, which is a fact about the
repository and not a claim about this service. Both sentences are appended by the node,
deterministically — the model writes the reasoning, the run writes the facts.

## Why

`platform` ships images tagged `501`. A build number is not a commit, and reading seven
hexadecimal digits as one is how an analysis ends up at a commit that does not exist. So
Phase 2 could get as far as the digest and stopped, and every F1 analysis ran at "the
last commit F0 summarised" and apologised for it in every ticket.

That build number *is* a tag in GitHub, so the fact is one read away. What it costs is a
fallback: when the tag is not there, the alternative to the default branch is not a
better commit, it is no commit at all — no analysis, no diagnosis, a ticket that says
Triage could not look.

Production runs the default branch in essentially every case. The case where it does not
is the one whose incident matters — a customer pinned to an older build, a hotfix
branch, a rollback — and the failure there is **quiet**: the analysis reads real code at
a real commit and answers confidently about a tree the tenant is not running. There is
no error to catch, no exception, nothing in a log. The only defence available is to make
the claim visible in the artefact a human reads.

Hence a cap rather than a refusal. The analysis is worth running; what it may not do is
present itself as confirmed. `medium` is exactly the level [ADR-0002](0002-confidence-thresholds.md)
defines as "the best available explanation, not a verified one", and it still clears the
F1 ticket threshold — so the developer gets the ticket *and* the caveat, which is the
trade this decision is making.

## Consequences

- `WorkloadEntry` carries `commit_source` and `commit_read_at`; `Deployment` and
  `Investigated` carry `commit_source` and `mapping_source` as two independent axes —
  which repository, and which commit.
- `CONFIDENCE_CAP` and `MAPPING_CONFIDENCE_CAP` are one-entry tables rather than
  branches, so adding a source that is also not an observation is a line, not a rewrite.
- A diagnosis can carry both caveats at once. It reads long; that is preferable to
  choosing which of two unverified claims to mention.

## Revisit when

A diagnosis is wrong in exactly this way — the ticket names a cause that is real on
`main` and not in the build the tenant runs. The fix then is **not** to distrust the
fallback generally, which would put Triage back to answering nothing: it is to get the
commit into the image tag, at which point rung 1 answers and this whole ladder stops
being reached. If instead the caveat starts appearing on every ticket, that is the
`config.yaml` tag templates being wrong or absent, not this rule.
