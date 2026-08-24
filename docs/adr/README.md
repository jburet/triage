# Architecture decisions

One file per decision, numbered. Each records the choice, why it was made, and
what would make it wrong — that last part matters most: a decision recorded
without its reversal condition cannot be revisited honestly.

0001-0012 resolve the twelve items marked `[OPEN]` in `../architecture.md`. They were
proposed rather than agreed: the alternative was leaving twelve blockers in place,
and every value that could be made configurable is in `config.yaml` so the SRE team
can override it without touching code.

0013 onwards record later changes to decisions that draft had already taken.

| # | Decision | Status |
|---|---|---|
| [0001](0001-platform-worker-count.md) | Platform worker count | Proposed |
| [0002](0002-confidence-thresholds.md) | Confidence levels and per-feature thresholds | Accepted, implemented |
| [0003](0003-recurrence-alerting.md) | Recurrence alerting on deduplicated tickets | Accepted, implemented |
| [0004](0004-bits-ai-unavailable.md) | Behaviour when Datadog Bits AI is unavailable | Superseded by 0016 |
| [0005](0005-secondary-cause-fanout.md) | Which hypotheses get analysed | Proposed |
| [0006](0006-f0-refresh-strategy.md) | F0 full vs incremental refresh | Proposed |
| [0007](0007-model-tiers-and-budgets.md) | Model tiers and spend guardrails | Accepted, implemented |
| [0008](0008-f3-database-access.md) | F3 target databases and credentials | Proposed |
| [0009](0009-analysis-job-result-channel.md) | How analysis Jobs return results | Proposed |
| [0010](0010-postmortem-destination.md) | Where the post-mortem draft is published | Proposed |
| [0011](0011-langgraph-platform-licence.md) | LangGraph Platform tier, and the fallback | Proposed |
| [0012](0012-backup-and-retention.md) | Backup and retention | Proposed |
| [0013](0013-jira-over-rest.md) | Jira over REST v3, not MCP | Accepted, implemented |
| [0014](0014-analysis-entrypoint-context-gather.md) | The analysis entrypoint gathers context, it does not agent | Accepted, implemented |
| [0015](0015-incremental-refresh-unit.md) | The unit of incremental refresh is the whole repository summary | Accepted, implemented |
| [0016](0016-datadog-collected-by-triage.md) | Triage collects Datadog telemetry itself, over REST | Proposed |
| [0017](0017-alert-ingestion-by-polling.md) | Alerts arrive by polling the Datadog event stream | Proposed |
| [0018](0018-alert-persistence-gate.md) | An alert is analysed only once it has persisted | Proposed |
| [0019](0019-workload-mapping-from-the-running-image.md) | A service is mapped to its repository by the image it is running | Proposed |
| [0020](0020-a-commit-nothing-observed-is-never-the-deployed-one.md) | A commit nothing observed is never presented as the deployed one | Proposed |
| [0021](0021-where-a-workload-is-defined-is-declared.md) | Where a workload is defined in its IaC repository is declared | Proposed |
| [0022](0022-one-client-and-a-malformed-tool-call-rate.md) | One client for both paths, and a measured malformed-tool-call rate | Proposed |

**Accepted, implemented** means M1 code depends on it. **Proposed** means it is
recorded so work can start, and nothing yet depends on it being right.
