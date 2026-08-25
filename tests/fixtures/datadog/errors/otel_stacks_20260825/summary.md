# Error Tracking capture — otel_stacks_20260825

Captured 2026-08-25T08:24:31.437006+00:00 by `make capture-errors`, read-only.
Window `2026-08-24T08:23:54.763831+00:00` .. `2026-08-25T08:23:54.763831+00:00` (24 h), query `env:prod`, persona `BACKEND`.

| track | issues | name a file | name a function | name both | with a version |
|---|---|---|---|---|---|
| trace | 184 | 184 | 184 | 184 | 8 |

## trace

- states: {'ACKNOWLEDGED': 74, 'OPEN': 100, 'IGNORED': 10}
- 102 distinct services, 22 distinct exception types
- 44 carry a `regression` block
- top exception types: [('zeenea.commons.exceptions.EntityNotFoundException', 70), ('org.postgresql.util.PSQLException', 69), ('zeenea.commons.exceptions.PropertyNotFoundException', 10), ('zeenea.service.api.ScannerUpsertItemException', 5), ('java.lang.NullPointerException', 5)]

## The reconstructed query, measured

No attribute joins an occurrence back to its issue, so a collector has to
rebuild the query from the issue's own fields. What that finds, against what
the issue claims over the same window:

| issue | query | issue claims | spans found | logs found |
|---|---|---|---|---|
| `7053abaa` | `service:plt-merck-qa @error.type:"zeenea.commons.exceptions.EntityNotFoundException"` | 199529 | None | None |
| `c634275c` | `service:plt-autostrade @error.type:"zeenea.commons.exceptions.EntityNotFoundException"` | 198360 | None | None |
| `30016244` | `service:plt-merck @error.type:"zeenea.commons.exceptions.EntityNotFoundException"` | 119550 | None | 6 |

## The occurrences, found by the query that works

`service:X status:error` over raw spans, joined on `exception.type` inside the
JSON string `custom.events` — the OpenTelemetry span events, where this platform
puts the type, the message and the stack. `@error.type` is empty here.

| service | error spans | with an OTel stack | the issue's type | matching | types actually retained |
|---|---|---|---|---|---|
| `plt-merck-qa` | 20 | 20 | `zeenea.commons.exceptions.EntityNotFoundException` | 0 | `zeenea.service.api.ScannerUpsertItemException` ×20 |
| `plt-autostrade` | 20 | 6 | `zeenea.commons.exceptions.EntityNotFoundException` | 0 | `java.net.ConnectException` ×4, `org.postgresql.util.PSQLException` ×2 |
| `plt-merck` | 20 | 20 | `zeenea.commons.exceptions.EntityNotFoundException` | 0 | `zeenea.service.api.ScannerUpsertItemException` ×20 |
| `plt-merck-dev` | 20 | 20 | `zeenea.commons.exceptions.EntityNotFoundException` | 0 | `zeenea.service.api.ScannerUpsertItemException` ×20 |

## Rate limits

10 of 16 calls carried an `X-RateLimit-*` header. Error Tracking published none.
