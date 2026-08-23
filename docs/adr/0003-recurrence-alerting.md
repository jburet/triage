# 0003 — Recurrence alerting on deduplicated tickets

Status: Accepted, implemented. Resolves architecture open item 3.

## Decision

Every dedup match posts a Slack notice and appends evidence to the existing
ticket. The notice **escalates** at the 3rd occurrence, then every 5th (3, 8, 13…).

`config.yaml`: `dedup_recurrence_alert: 3`, `dedup_recurrence_interval: 5`.

## Why

Two separate needs, met by one message with two tones.

**Every match is announced** because a false dedup match is the worst failure this
component has: it appends a new incident's evidence to an unrelated ticket and
nobody sees it again. A quiet "recurrence #2 of PAY-7, evidence appended" makes a
wrong match visible within seconds. Silence would not.

**Escalation is rationed** because a recurring problem that is already ticketed
does not become more urgent by being announced loudly every time. Three
occurrences is where "it happened again" turns into "this is not going away".

The model is also not trusted with the ticket key. `dedup_check` is shown a
shortlist from the database and any key outside that shortlist is discarded, with
the discarded reasoning preserved in state. The dedup prompt is explicitly biased
towards `matched: false`: a duplicate ticket costs a human seconds, a false match
costs an incident.

## Revisit when

Teams report the quiet notices as noise, or a false match reaches production
undetected. The first argues for suppressing occurrences 2 through the escalation
point; the second argues the opposite and would mean the dedup prompt, not this
schedule, needs work.
