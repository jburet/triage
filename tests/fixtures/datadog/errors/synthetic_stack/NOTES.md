# This fixture is hand-written, and that is the whole point

`logs_error_sample.json` is **not a capture**. Nothing in this directory came
back from Datadog. It is written by hand, on 2026-08-25, to test one rule that
the org's telemetry currently gives no way to test: *the template reduction must
not eat the stack trace* (M8 behaviour 3.1).

Every other fixture under `tests/fixtures/datadog/` is a permanent record of a
real response. This one is a labelled counterexample, and it is kept apart from
`org_20260825_1h/` so nobody ever reads a number out of it as though it were
measured.

## Why there is no real one

Probed against the live org on 2026-08-25 (see
[ADR-0027](../../../../../docs/adr/0027-an-absence-datadog-discards-is-a-finding.md)),
over the reference hour `04:35Z`–`05:35Z`:

- `service:plt-systeme-u-rec` ships **11 log events an hour**, none of them at
  `status:error`, against 5,869 exceptions Error Tracking counted for it.
- `@error.stack:*` over logs, org-wide, returns **two services** —
  `hosted-langserve-backend` (920) and `sql-to-lineage-prod-euw3` (47) — and
  neither is a `plt-*` tenant.
- `@error.stack:*` over spans, org-wide, returns **zero**.

So no service F2 reports on ships a stack trace to a store F2 can read, and one
cannot be captured by waiting. A rule tested against nothing is a guess; a rule
tested against a labelled synthetic payload is proven but unobserved. Those are
different claims and this file is what keeps them apart. When the "Error Default"
retention filter is enabled and real error telemetry starts arriving, this
fixture should be replaced by a capture and deleted.

## What it contains

Nine log events for `plt-systeme-u-rec`, shaped as
`POST /api/v2/logs/events/search` returns them:

- Six carry the message shape `Error in query «load_contact_by_id» with (id ->
  contact:…) : Not found` with a different id each time, and three carry
  `load_inventory_item_by_path`. Templated, the nine collapse to **two**
  templates — which is the reduction doing its job.
- **Exactly one** of the nine (index 4) carries
  `attributes.error.stack`: a nine-frame Scala stack ending in
  `OdbClient.scala:412`, the line the Error Tracking issue names. One and not
  nine, because the rule under test is that the reduction finds the stack
  wherever it is and returns it *whole* — not that it happens to keep the newest
  line.

The stack is 664 bytes, longer than any clip the reduction applies to a message
(400) or to a template (200). If either limit ever leaks onto it, the test fails.
