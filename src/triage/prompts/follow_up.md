You have the result of a fixed sweep around a production alert. Decide whether
you need **further calls** to explain what happened, and if so, exactly which.

You may request calls only from the collector list you are given, and only within
the remaining budget. A request naming anything else is discarded and recorded as
a discarded request, which helps nobody.

Ask for a further call when:

- A collector surfaced something whose *detail* you cannot see — an event whose
  before/after you want at another scope, a log template whose full lines you
  need, a metric that would confirm or kill a mechanism the sweep only suggested.
- A collector came back empty at one scope and the same signal plausibly exists at
  another (`kube_namespace:` rather than `service:`, the pod rather than the
  workload). The reference incident's container exit codes and probe failures were
  visible only at namespace scope.
- A change event was reported and you need the object specification on both sides
  of it to tell a deployment from a status flapping.

Do **not** ask for a further call when:

- The sweep already answers the question. Say `done: true` and stop; every call
  costs latency during an incident and tokens the diagnosis still needs.
- You are hoping something turns up. A query you cannot justify in `why` is a
  query you should not make.
- The signal is marked `not_instrumented`. Asking again will return the same
  nothing.

For each request give the collector, the exact Datadog query string, and what it
would settle that the sweep did not.
