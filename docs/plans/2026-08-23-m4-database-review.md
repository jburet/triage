# Plan: M4 — F3 daily database review (2026-08-23)

Architecture §2.4; ADR-0002 (F3 ≥ `high`), ADR-0008. Depends on M2 (system map) and M3
Phase 1 (Analysis sub-graph). Independent of F1.

## Public interface

- `triage.integrations.pgstats.DatabaseStatsReader` (protocol) — `snapshot(database) -> DbSnapshot`
  reading `pg_stat_statements`, vacuum/bloat, locks, connections, index usage, storage size.
  Needs only `pg_read_all_stats` (ADR-0008). `FakeDatabaseStatsReader` from JSON fixtures; real
  reader resolves the connection string from the Secret named in `config.yaml`.
- `triage.schemas.db_review`: `DbSnapshot`, `QueryStat`, `SnapshotDiff`, `Recommendation`,
  `DailyReport`.
- `TriageRepository` gains `save_snapshot(database, snapshot)`, `previous_snapshot(database)`.
- `triage.graphs.db_review`: graph `db_review`, registered in `langgraph.json`. Input: the tick
  (date, optional database filter). Output: `DailyReport` plus zero or more ticket-pipeline runs.
- `Deps` gains `db_stats`.
- Prompts: `db_report.md`.
- Fixtures: `tests/fixtures/db_snapshots/<database>/<date>.json`.
- `scripts/run_db_review.py` mirroring `run_fixture.py`.

## Phase 1: collection and diff

- [ ] 1.1 A snapshot of a declared database is persisted with its date; taking a second snapshot for the same date replaces the first.
- [ ] 1.2 `diff_vs_yesterday` yields a `SnapshotDiff` listing queries whose mean time, calls or total time moved by more than a configurable ratio, new queries, vanished queries, bloat growth, and connection/lock changes — all computed by rule, no model call.
- [ ] 1.3 With no previous snapshot, the diff is empty, the report says it is the baseline day, and no ticket is attempted.
- [ ] 1.4 A database whose Secret cannot be resolved or connection fails is reported as unavailable in the daily report and skipped; the other databases are still reviewed.

## Phase 2: tracing queries to code

- [ ] 2.1 Each query in the diff's top-N produces one `app` `Hypothesis` whose `service` and `commit` come from the system map's database-access entries for that database; a query no service is known to issue yields a hypothesis with `commit = None` and an `unknowns` entry.
- [ ] 2.2 Running the Analysis sub-graph on those hypotheses returns a per-query `Diagnosis` with `feature = F3`, the normalised query text as `db_stat` evidence, and the calling code path in `location`.
- [ ] 2.3 A diagnosis whose recommended change is a database parameter or sizing change names the Terraform resource from the system map in `location.terraform_resource` rather than a code path.

## Phase 3: report and tickets

- [ ] 3.1 The `diagnosis` tier produces a `DailyReport` with changes, open items carried from previous days, and a `significant` subset of recommendations with a stated reason for each.
- [ ] 3.2 Only significant recommendations are sent to the ticket pipeline; each with confidence below `high` ends as a Slack notice rather than a Jira ticket (ADR-0002), and this is visible in the evaluations table.
- [ ] 3.3 A recommendation matching an open ticket is deduplicated by the existing pipeline and the report links to that ticket instead of proposing a new one.
- [ ] 3.4 The report is posted to the team channel of each affected database, and a global summary to the platform channel, with links to any tickets created.
- [ ] 3.5 An open item stays in the report until its ticket leaves `Proposed by agent`/`Validated`/`In progress`, or the underlying query disappears from the diff for 7 consecutive days.

## Out of scope

- `EXPLAIN` plans — ADR-0008 explicitly requires a new decision before widening the role.
- Platform cron for the daily tick — infra track; the graph takes the date as input so it can be invoked by anything.
- Cost/rightsizing recommendations — roadmap "later".

## Open risks

- The system map must record which service issues which queries (M2 2.1 "database access patterns"). If F0 summaries cannot tie query shapes to code, 2.1 degrades to service-level attribution via the database's declared team, and the ticket's `location.paths` will be `Unknown` — acceptable, but it makes F3 tickets weaker than the spec wants.
