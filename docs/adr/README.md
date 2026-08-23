# Architecture decisions

One file per decision, numbered. Each records the choice, why it was made, and
what would make it wrong — that last part matters most: a decision recorded
without its reversal condition cannot be revisited honestly.

0001-0012 resolve the twelve items marked `[OPEN]` in `../architecture.md`. They were
proposed rather than agreed: the alternative was leaving twelve blockers in place,
and every value that could be made configurable is in `config.yaml` so the SRE team
can override it without touching code.

0013 records a later change to a decision that draft had already taken.

| # | Decision | Status |
|---|---|---|
| [0001](0001-platform-worker-count.md) | Platform worker count | Proposed |
| [0002](0002-confidence-thresholds.md) | Confidence levels and per-feature thresholds | Accepted, implemented |
| [0003](0003-recurrence-alerting.md) | Recurrence alerting on deduplicated tickets | Accepted, implemented |
| [0004](0004-bits-ai-unavailable.md) | Behaviour when Datadog Bits AI is unavailable | Proposed |
| [0005](0005-secondary-cause-fanout.md) | Which hypotheses get analysed | Proposed |
| [0006](0006-f0-refresh-strategy.md) | F0 full vs incremental refresh | Proposed |
| [0007](0007-model-tiers-and-budgets.md) | Model tiers and spend guardrails | Accepted, implemented |
| [0008](0008-f3-database-access.md) | F3 target databases and credentials | Proposed |
| [0009](0009-analysis-job-result-channel.md) | How analysis Jobs return results | Proposed |
| [0010](0010-postmortem-destination.md) | Where the post-mortem draft is published | Proposed |
| [0011](0011-langgraph-platform-licence.md) | LangGraph Platform tier, and the fallback | Proposed |
| [0012](0012-backup-and-retention.md) | Backup and retention | Proposed |
| [0013](0013-jira-over-rest.md) | Jira over REST v3, not MCP | Accepted, implemented |

**Accepted, implemented** means M1 code depends on it. **Proposed** means it is
recorded so work can start, and nothing yet depends on it being right.
