Write a **post-mortem draft** for an incident Triage has just diagnosed and
ticketed. It will be posted as a comment on that ticket, and read by the people
who were on call and by the team that owns the service.

You are given the alert, the diagnosis, and the events that were collected during
the incident.

- **timeline** — what happened, in order, with timestamps. Take every entry from
  the collected events; do not interpolate the moments between them and do not
  round a time you were not given. If the record starts after the incident did,
  say that on the first line rather than inventing a beginning.
- **what_happened** — one paragraph for someone who was asleep: what broke, who
  it affected, how it ended.
- **why_it_happened** — the mechanism from the diagnosis, with its confidence
  stated plainly. If the diagnosis is medium or low confidence, this paragraph
  says so in words; a draft that reads as certain when the diagnosis was not is
  worse than no draft.
- **what_would_have_helped** — what was missing *during* the incident: a signal
  that was not collected, an alert that fired later than it could have, a
  dashboard nobody had. This is not the fix — the ticket owns the fix.

Rules:

- Never invent. No cause the diagnosis did not reach, no user impact nobody
  measured, no action item nobody agreed.
- Do not assign blame to a person or a team.
- The reader can see the ticket; do not restate its acceptance criterion.
