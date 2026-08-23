# 0008 — F3 target databases and credentials

Status: Proposed. Resolves architecture open item 8.

## Decision

Target databases are declared in `config.yaml` under `databases`, each naming a
Kubernetes Secret rather than carrying a credential:

```yaml
databases:
  - name: payments-primary
    team: payments
    secret_ref: triage-db-payments-primary
```

Each Secret provides a connection string for a role created as:

```sql
CREATE ROLE triage_ro LOGIN PASSWORD :'pw';
GRANT pg_read_all_stats TO triage_ro;
GRANT CONNECT ON DATABASE :db TO triage_ro;
```

No table-level `SELECT` is granted.

## Why

**Explicit list, not discovery.** Triage should never connect to a production
database nobody added deliberately. The list also carries team ownership, which is
what routes an F3 ticket to a board.

**`pg_read_all_stats` and nothing else.** F3 reads `pg_stat_statements`, vacuum and
bloat state, lock and connection counts, index usage. All of that lives in
statistics views. It needs no access to application rows, and `pg_stat_statements`
normalises query text, so query shapes are visible without literal values. A role
that cannot read customer data cannot leak it into a Jira ticket — a property
worth having structurally rather than by convention.

**Secret references, not values.** `config.yaml` is versioned; credentials must not
be. The indirection also means rotating a password never touches this repository.

## Revisit when

F3 needs `EXPLAIN` plans, which require more than statistics access. That is a
real widening of the blast radius and deserves its own decision rather than a
quiet grant.
