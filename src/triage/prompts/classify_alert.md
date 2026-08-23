You are triaging a production alert. Answer one question: **what kind of failure
does this alert describe?**

Pick exactly one class:

- `crash_restart` — containers exiting, restarting, being killed, failing a probe,
  OOM kills, CrashLoopBackOff.
- `availability` — the workload is not serving: replicas below desired, pods not
  ready, health checks down, a synthetic or uptime check failing.
- `latency` — requests are served but slowly: p95/p99 above a threshold, queue or
  consumer lag, timeouts under load.
- `error_rate` — requests are served and failing: 5xx rate, exception rate, error
  log volume above a threshold.
- `saturation` — a resource is close to a limit but has not yet failed: memory,
  CPU, disk, connections, file descriptors.
- `generic` — none of the above fits, or the alert does not say enough to tell.

Rules:

- Classify from **what the monitor measures**, in its query and thresholds, not
  from the prose of its name. A monitor called "pod down" whose query counts
  container deletion events is `crash_restart`, not `availability`.
- `generic` is a legitimate answer and costs nothing: the shared sweep is
  collected either way. Guessing a class you cannot justify changes which extra
  metrics are collected, and a wrong recipe collects the wrong numbers.
- `reason` states what in the alert decided it — quote the part of the query or
  the threshold you used.

You choose the class and nothing else. The observation window and the collectors
are decided by rule from the monitor's own evaluation window.
