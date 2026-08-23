# The developable-ticket specification

**Normative.** This document defines what Triage must produce. The Pydantic models
in `src/triage/schemas/` are its executable form, and the `compose_ticket` and
`self_review` prompts are written against it. If this document and the code
disagree, this document is right and the code is a bug.

A ticket is complete only if **a developer could start working on it without
asking a question.**

## The nine sections

Every one is mandatory. A section with nothing to report says so and says why; it
is never blank, and never "N/A".

| # | Section | Must contain |
|---|---|---|
| 1 | **Symptom** | What was observed, with the measured numbers and the time window. |
| 2 | **Impact** | Which users, which services, which SLOs. |
| 3 | **Probable cause** | The mechanism, with a confidence level. |
| 4 | **Evidence** | Links to the metrics, logs, traces and Kubernetes events that support it. |
| 5 | **Location** | Repository, deployed commit, suspected files or functions — or the Terraform module and resource. |
| 6 | **Expected change** | A verifiable acceptance criterion the developer checks before closing, plus where they check it. |
| 7 | **Out of scope** | What the fix must not touch. |
| 8 | **Hypotheses ruled out** | What was eliminated, and the observation that eliminated it, so nobody redoes the work. |
| 9 | **Unknowns** | What is still not known, and why it could not be determined. |

## The rules behind the sections

**Never invent.** Any field that cannot be filled with confidence is marked
unknown, with a reason. This is not a style preference: a ticket that asserts a
plausible-looking commit hash costs a developer more time than a ticket that
admits it does not know which commit is implicated.

In the schemas this is structural rather than advisory. A field that may be
absent has type `Filled | Unknown`, where `Unknown` carries a mandatory `reason`;
`Filled` rejects placeholder prose. There is no representation for "empty".

**Confidence must be earned.** `Diagnosis` refuses two combinations outright: a
cause it cannot name paired with anything above low confidence, and high
confidence resting on a single piece of evidence.

**Numbers, not adjectives.** "p95 rose from 120 ms to 1.4 s", not "latency
degraded".

**Specify the outcome, not the fix.** Triage diagnoses and specifies; the
developer decides how to fix it. Section 6 states the outcome to reach, never the
code to write.

**An unknown, stated with its reason, is a passing section.** Self-review must not
fail a ticket for honesty. Doing so is what pressures a model into inventing.

## Jira workflow

| State | Set by | Meaning |
|---|---|---|
| `Proposed by agent` | Triage | Created automatically, routed to the owning team's board. |
| `Validated` | A human — lead dev or SRE | The ticket enters the team's backlog. No automated action follows. |
| `In progress` / `Done` | The developer | Standard. Closed after checking the section 6 criterion. |

Triage writes only the first transition. Everything after it is a human's.

## When there is no ticket

Three cases, all of which post to Slack and record an evaluation row:

- **Below the confidence threshold** — the notice carries the symptom, the best
  guess and the open questions. A signal Triage was unsure about is the one a
  human most needs to see.
- **Self-review exhausted** — the draft is attached for a human to finish. Filing
  a ticket the reviewer just rejected would put the investigation burden back on
  the developer, which is the thing Triage exists to remove.
- **Deduplicated** — evidence is appended to the existing ticket and an occurrence
  counter incremented. Every match is announced, so a wrong match is visible
  rather than silent.

## How this is verified

- `tests/unit/test_schemas.py` — the nine sections are mandatory, placeholders are
  rejected, unearned confidence is refused.
- `tests/integration/test_prompt_inputs.py` — nothing the diagnosis knows is lost
  on the way to the model, and unknowns arrive as marked absences.
- `evals/` — whether the resulting prose is actually usable. Scored, not asserted.
