# 0009 — How analysis Jobs return results

Status: Proposed. Resolves architecture open item 9.

## Decision

A PostgreSQL table, `triage.analysis_results`, keyed by Job name. The Job writes
its result with a narrow role that may insert and update that table and nothing
else. The NetworkPolicy for analysis Jobs is extended to reach the Platform's
PostgreSQL, alongside GitHub and the LiteLLM proxy.

Job timeout 15 minutes. Clone depth 1, `--filter=blob:none` when two commits are
needed for a diff.

## Why

Three channels were possible.

**Job logs** need no new network path, which is genuinely attractive for a
sandboxed workload. But they cap result size, mix the payload with anything the
Agent SDK writes to stdout, and are subject to the cluster's log rotation — a
result that has to be parsed out of a log stream fails in ways that are tedious
to debug at 3 a.m.

**An object store** is unbounded and durable, but it is a new component to deploy,
secure and back up for one narrow purpose.

**A table** costs one extra egress rule and a narrow role. In exchange the result
is structured, queryable, unbounded in size, survives Job deletion, and is
recoverable by the same backup that covers everything else (ADR-0012). The graph
node polls one row rather than tailing a log.

The security trade is the honest cost: the sandbox gains a path to the database.
It is mitigated by the role, which can write one table and read nothing.

15 minutes is long enough for a large repository summary and short enough that a
wedged Job does not hold a queue slot for an hour. Shallow clones because the
analyses read a tree at a commit, not a history.

## Revisit when

Results routinely exceed a few megabytes — at that point the object store's
advantages start to matter and the extra component earns its keep.
