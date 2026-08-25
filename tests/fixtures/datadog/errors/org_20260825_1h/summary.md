# Error Tracking capture — org_20260825_1h

Captured 2026-08-25T05:35:32.076312+00:00 by `make capture-errors`, read-only.
Window `2026-08-25T04:35:24.520177+00:00` .. `2026-08-25T05:35:24.520177+00:00` (1 h), query `env:prod`, persona `BACKEND`.

| track | issues | name a file | name a function | name both | with a version |
|---|---|---|---|---|---|
| trace | 15 | 15 | 15 | 15 | 0 |
| logs | 0 | 0 | 0 | 0 | 0 |

## trace

- states: {'ACKNOWLEDGED': 10, 'IGNORED': 1, 'OPEN': 4}
- 12 distinct services, 6 distinct exception types
- 3 carry a `regression` block
- top exception types: [('zeenea.commons.exceptions.EntityNotFoundException', 9), ('com.orientechnologies.orient.core.exception.ODatabaseException', 2), ('java.lang.NullPointerException', 1), ('java.lang.IllegalArgumentException', 1), ('zeenea.commons.exceptions.PropertyNotFoundException', 1)]

## The reconstructed query, measured

No attribute joins an occurrence back to its issue, so a collector has to
rebuild the query from the issue's own fields. What that finds, against what
the issue claims over the same window:

| issue | query | issue claims | spans found | logs found |
|---|---|---|---|---|
| `482cc960` | `service:plt-systeme-u @error.type:"zeenea.commons.exceptions.EntityNotFoundException"` | 6344 | None | None |
| `395eb060` | `service:plt-systeme-u-rec @error.type:"zeenea.commons.exceptions.EntityNotFoundException"` | 5869 | None | None |
| `3764445e` | `service:plt-autostrade @error.type:"zeenea.commons.exceptions.EntityNotFoundException"` | 4009 | None | None |

## Rate limits

6 of 13 calls carried an `X-RateLimit-*` header. Error Tracking published none.
