# Plan: M5 — self-evaluation, incident memory, alert coverage audit (2026-08-23)

Roadmap "Cross-cutting features"; architecture §4 (`evaluations`), §8 (Jira webhook on the
ingress). Depends on M3 (ingress, F1). Three independent phases — they can be built in any order
and each is a separate `/tdd` session.

## Public interface

- `POST /webhooks/jira` on the ingress — receives issue transition events for tickets Triage created.
- `TriageRepository` gains `record_validation(jira_key, validated, feedback)`,
  `record_closure(jira_key, feedback)`, `update_ticket_state(jira_key, state)`,
  `similar_tickets(service, query, limit)`, `evaluation_summary(since)`.
- `triage.schemas.evaluation`: `EvaluationSummary` (first-time-right rate, validation rate,
  median time-to-ticket, false-positive count, per feature and per team).
- `triage.graphs.alert_audit`: graph `alert_audit`. Input: the tick. Output: `AlertAuditReport`.
- `DatadogClient` gains `list_monitors()`, `list_slos()`, `monitor_history(monitor_id, window)`.
- `scripts/evaluation_report.py` prints the summary for a period.
- Prompts: `alert_audit.md`, `incident_memory.md`.

## Phase 1: self-evaluation from Jira feedback

- [ ] 1.1 A Jira transition webhook for a ticket Triage created updates the mirrored `state` on the `tickets` row; a webhook for an unknown key is acknowledged and ignored.
- [ ] 1.2 A transition to `Validated` records `validated = true` and the transition comment as `reviewer_feedback_validation` on the evaluation row linked to that ticket; a transition to a rejected/closed-without-fix state records `validated = false`.
- [ ] 1.3 A transition to `Done` records the closing comment as `reviewer_feedback_closure`.
- [ ] 1.4 `evaluation_summary(since)` reports, per feature and per team: tickets proposed, validated rate, first-time-right rate (validated with `compose_attempts = 1`), median time-to-ticket, and false positives (proposed then rejected) — computed from rows, no model call.
- [ ] 1.5 A monthly Slack post of that summary goes to the platform channel (the scheduling hook is the same as F3's tick; cron registration is infra).

## Phase 2: incident memory

- [ ] 2.1 `similar_tickets(service, query)` returns past tickets for the service ranked by similarity of summary and probable cause, including closed ones, excluding the ticket being composed.
- [ ] 2.2 When composing a new ticket, up to three similar past tickets are offered to the `analysis` tier as context, and the draft's `evidence` or `ruled_out` cites them by key only when the model actually used them — a cited key the model was not shown is dropped, as dedup already does.
- [ ] 2.3 The created Jira issue links to the cited past tickets with a "relates to" link, and the Slack notice names them.
- [ ] 2.4 Incident memory never changes the dedup decision: a similar closed ticket is context, not a match.

## Phase 3: alert coverage audit

- [ ] 3.1 For each service in the system map, the audit lists its monitors and SLOs from Datadog and flags services with none as uncovered.
- [ ] 3.2 A monitor that fired more than a configurable number of times over the window without ever producing a validated ticket is flagged noisy; one whose target (service, endpoint, resource) no longer exists in the system map is flagged obsolete.
- [ ] 3.3 The `analysis` tier turns the flags into an `AlertAuditReport` with one recommendation per finding, each naming the monitor or the missing coverage and the owning team.
- [ ] 3.4 The report is posted to the platform channel; recommendations are not sent to the ticket pipeline (they are SRE work, not developer tickets).

## Out of scope

- Change correlation beyond deployments, cost recommendations — roadmap items with no design yet; recommend `/grill` before planning.
- Automated ticket verification and post-release monitoring — removed from scope by the roadmap.

## Open risks

- Phase 1 assumes Jira exposes the transition comment in the webhook payload. If reviewers leave feedback as a separate comment, 1.2/1.3 need a `get_comments` call on `JiraClient` and a "latest comment by a human since transition" rule.
- Phase 2's similarity is assumed to be done by the `triage` tier over candidate summaries, not by embeddings — no vector store exists and ADR-0012 does not plan one. If the candidate set per service grows past what a prompt can hold, that becomes a new decision.
- The roadmap names false positives as a metric but "rejected" has no Jira state yet. Phase 1 needs the workflow to define one (or treat `Won't do` as rejection) — confirm with the SRE team, who own the workflow.
