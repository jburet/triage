# 0005 — Which hypotheses get analysed

Status: Proposed. Resolves architecture open item 5.

## Decision

Analyse the top 3 hypotheses with `rank_score >= 0.3`, and always at least 1.

`config.yaml`: `analysis.min_rank_score: 0.3`, `analysis.max_hypotheses: 3`.

## Why

Each analysed hypothesis is a Kubernetes Job, a repository clone and a Sonnet run.
The cost is real and it is per hypothesis, so the fan-out needs a bound.

The floor and the ceiling do different jobs. `min_rank_score` skips hypotheses the
qualifier itself considers unlikely — analysing those spends money to produce a
"ruled out" line nobody needed. The cap of 3 bounds the worst case when the
qualifier is uncertain and spreads its score thinly across many candidates, which
is exactly when it is least worth following each one.

"Always at least 1" prevents the degenerate case: a qualifier with no confidence
anywhere produces an empty fan-out and a diagnosis with no code analysis at all,
which is the one thing Triage is for.

Hypotheses that were considered but not analysed still belong in the ticket's
`ruled_out` section where the qualifier had a reason — the point of that section
is that nobody redoes the work.

## Revisit when

Tickets are regularly reopened because the real cause was a hypothesis ranked
fourth. Raise the cap before lowering the floor: the failure would be in breadth,
not in the score.
