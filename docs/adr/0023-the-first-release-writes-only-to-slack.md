# 0023 — The first release writes only to Slack

Status: Proposed. Postpones Jira creation and F3; narrows ADR-0002's thresholds to framing.

## Decision

The first release Triage puts in front of the SRE team writes **only to Slack**. Jira issue
creation is gated off in configuration — the client, the composer and the self-review stay
in the tree and stay tested, and no run calls Jira. F3, the daily database review, is not
built.

What ships is F1 whole: a Datadog alert that has persisted, collected, qualified, analysed
against the code and the infrastructure code at the deployed commit, diagnosed, and posted
to the owning team's channel as one threaded report.

Confidence stops deciding *where* a diagnosis goes and starts deciding *how it is framed*.
Every diagnosis reaches the team; a `low` one arrives saying what is established and what is
not, which is what both live runs on 2026-08-24 produced and what was thrown away.

## Why

Three things learned on 2026-08-24, running F1 against a real alert for the first time.

**The pipeline already ends in Slack.** Both end-to-end runs finished at
`notify_below_threshold`, and correctly: no analysis can run without the Job image, so no
mechanism is ever substantiated, so confidence can never exceed `low`, so ADR-0002's gate
sends every real incident to Slack anyway. Gating Jira off changes almost nothing about what
happens today — it makes the honest thing explicit instead of incidental.

**The valuable content is computed and discarded.** The qualification produced a minute-by-minute
timeline with numbers, four ranked candidate mechanisms, four causes ruled out with the
evidence that ruled them out, and six named unknowns each with a reason. The diagnosis added
nine evidence items across events, metrics and logs. All of it was reduced to a four-line
Slack notice reading "No ticket raised — confidence low". The product exists; only the
delivery does not.

**Nothing has ever been written to Jira.** `JiraClient` is written from the REST reference and
has never met a live instance (§10). Shipping a first release that depends on it means
debugging an unverified write path at the same time as the first real user is deciding whether
any of this is worth reading.

A ticket also asks for a second queue. `Proposed by agent` needs a human to validate before
the work enters a backlog, and that human has no reason to trust the agent yet. A message in
the channel where the team already handles alerts asks for nothing and can be judged on the
spot. Jira becomes worth building when someone asks to keep one of these reports.

## Consequences

- `ADR-0002`'s per-feature thresholds keep their values and change their meaning: the
  threshold decides whether the report leads with a cause or with what is established. The
  `notify_below_threshold` / `create_ticket` split becomes one renderer with two framings.
- `docs/ticket-spec.md`'s nine sections become the spec for the **report**, not the issue.
  Nothing about what a developer needs in order to act changes because the destination did.
- **Deduplication and the recurrence escalation (ADR-0003) do not run at all.** This
  paragraph first claimed they "matter more, not less", which was wrong about the code:
  `dedup_check` shortlists from `open_tickets_for_service`, and only `create_ticket` ever
  writes that table. With nothing filed there is nothing to match, so every recurrence is a
  fresh report — the third pod-down of a night reads exactly like the first, with no "this
  is the 3rd time" and no thread continuity across cycles. Recurrence waits for Jira.

  Making it work without Jira means persisting reports and deduplicating against those
  instead, which is a second store of what was said, with its own staleness and its own
  reopen semantics — the ticket queue, rebuilt in the database, for a release whose whole
  argument is that it does not need one. The cheaper answer is that a team reading its own
  alert channel already sees repetition: the alert fires again in the same channel, above
  the report. Triage adds nothing to that today, and says nothing false about it either.
- The Jira client, `compose_ticket` and `self_review` stay under test against fakes. They
  are not deleted, so the decision is reversible by configuration.
- F3 and the ingress leave the critical path entirely. The ingress existed for the GitHub
  and Jira webhooks; F1 is polled (ADR-0017), so nothing in this release needs an inbound
  HTTP surface.

## What this costs

Recurrence, most of all. A report that cannot say "again" is the weakest thing about this
release, and the one an on-call reader will notice first — repetition is often the whole
finding, and Triage will be silent about it while Datadog is not.

The self-review loop is the quality gate that ADR-0002 built, and a Slack message that
nobody must validate has a weaker one. The report is judged by whoever reads the channel,
and if nobody reads it, nothing says so. The first thing to measure after this ships is
whether the reports are read and acted on at all — which is what M5's evaluation was for and
which now has no Jira transition to learn from.

## Revisit when

Someone asks to keep a report — to assign it, schedule it, or track that it was fixed. That
request is the evidence that the ticket path is worth the second queue, and the code to serve
it is still in the tree.

Sooner, and more likely: someone asks why the same incident was reported three times without
Triage noticing. That is recurrence being wanted, and it brings Jira with it rather than a
store built to avoid it — unless the ask is specifically for the *count* and not the queue,
in which case persisting reports is the smaller answer after all. Revisit also if the channel becomes noise: that means the
persistence gate or the recurrence rule is wrong, and a ticket would not have fixed it.
