# The developable-report specification

**Normative.** This document defines what Triage must produce. The Pydantic models
in `src/triage/schemas/` are its executable form, and `src/triage/report.py`
renders against it — as do the `compose_ticket` and `self_review` prompts, on the
Jira path that configuration currently switches off (ADR-0023).

A report is complete only if **a developer could start working on it without
asking a question.**

Nothing about *what a developer needs in order to act* changed when the
destination did. The nine sections below are the same nine sections; "ticket" and
"report" name the same content in two envelopes, and this file keeps its name so
that every reference to it stays a reference to it.

## The nine sections

Every one is mandatory. A section with nothing to report says so and says why; it
is never blank, and never "N/A".

| # | Section | Must contain |
|---|---|---|
| 1 | **Symptom** | What was observed, with the measured numbers and the time window. |
| 2 | **Impact** | Which users, which services, which SLOs. |
| 3 | **Probable cause** | The mechanism, with a confidence level. |
| 4 | **Evidence** | Links to the metrics, logs, traces and Kubernetes events that support it. |
| 5 | **Location** | Repository, deployed commit, suspected files or functions — or the Terraform module and resource — and what said so. |
| 6 | **Expected change** | A verifiable acceptance criterion the developer checks before closing, plus where they check it. |
| 7 | **Out of scope** | What the fix must not touch. |
| 8 | **Hypotheses ruled out** | What was eliminated, and the observation that eliminated it, so nobody redoes the work. |
| 9 | **Unknowns** | What is still not known, and why it could not be determined. |

## The rules behind the sections

**Never invent.** Any field that cannot be filled with confidence is marked
unknown, with a reason. This is not a style preference: a report that asserts a
plausible-looking commit hash costs a developer more time than one that admits it
does not know which commit is implicated.

In the schemas this is structural rather than advisory. A field that may be
absent has type `Filled | Unknown`, where `Unknown` carries a mandatory `reason`;
`Filled` rejects placeholder prose. There is no representation for "empty".

**Confidence must be earned.** `Diagnosis` refuses two combinations outright: a
cause it cannot name paired with anything above low confidence, and high
confidence resting on a single piece of evidence.

**Confidence frames, it does not route.** Every diagnosis reaches the owning team
(ADR-0023). The per-feature threshold (ADR-0002) decides whether the report leads
with the probable cause or with what is established and what is missing — not
whether it is sent.

**A location says what said so.** The repository and the commit each carry the
rung that produced them (ADR-0019, ADR-0020): a repository derived from the
running image and one matched by a `serves` name pattern are different facts, and
must not read alike. The same holds for a commit read from an image tag and one
read from a default branch.

**Numbers, not adjectives.** "p95 rose from 120 ms to 1.4 s", not "latency
degraded".

**Specify the outcome, not the fix.** Triage diagnoses and specifies; the
developer decides how to fix it. Section 6 states the outcome to reach, never the
code to write.

**An unknown, stated with its reason, is a passing section.** Self-review must not
fail a report for honesty. Doing so is what pressures a model into inventing.

## Where it is delivered

One threaded message in the owning team's Slack channel, under the notice
`open_incident` posted when Triage started looking (ADR-0017). Every later notice
about the same incident replies into that thread, so a recurring problem reads as
one conversation.

A report longer than one Slack message is split between sections, never inside
one, and each part says which part it is.

## Jira workflow — postponed

**Not built into any shipped configuration** (ADR-0023). The client, the composer
and the self-review remain in the tree and remain tested against fakes;
`writes: slack_and_jira` in `config.yaml` restores the path below. Nothing has
ever been written to a live Jira instance.

| State | Set by | Meaning |
|---|---|---|
| `Proposed by agent` | Triage | Created automatically, routed to the owning team's board. |
| `Validated` | A human — lead dev or SRE | The ticket enters the team's backlog. No automated action follows. |
| `In progress` / `Done` | The developer | Standard. Closed after checking the section 6 criterion. |

Triage writes only the first transition. Everything after it is a human's.
Revisit when someone asks to *keep* a report — to assign it, schedule it, or
track that it was fixed.

## When the report is not a fresh one

- **Deduplicated** — on the Jira path, evidence is appended to the existing ticket
  and an occurrence counter incremented; every match is announced, so a wrong
  match is visible rather than silent (ADR-0003). With nothing filed there is no
  ticket to reopen, so a recurrence is a repeating message, and the thread is
  what keeps it legible.
- **Self-review exhausted** — a Jira-path outcome only. There is no filing
  decision to exhaust when the report is the destination, so the same run posts
  the report.

## How this is verified

- `tests/unit/test_schemas.py` — the nine sections are mandatory, placeholders are
  rejected, unearned confidence is refused.
- `tests/integration/test_report.py` — the rendered report carries every section,
  states the mapping rung, and threads.
- `tests/integration/test_prompt_inputs.py` — nothing the diagnosis knows is lost
  on the way to the model, and unknowns arrive as marked absences.
- `evals/` — whether the resulting prose is actually usable. Scored, not asserted.
