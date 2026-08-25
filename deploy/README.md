# The cluster objects Triage names

`config.analysis.job` names a namespace, a ServiceAccount and a Secret;
`triage.analysis.jobs` submits a Job into them. Until this directory existed, none of those
things did. These are the manifests for them.

**Nothing here has ever been applied to a cluster.** They validate — `kubeconform -strict
-kubernetes-version 1.31.0 deploy/*.yaml` passes, and `tests/unit/test_deploy_manifests.py`
holds the Job template to the object Triage actually POSTs — but validation is not
admission, and admission is not the sandbox working. The first `kubectl apply` is the first
contact (plan M7 4.2 and 4.4).

## Apply order

| | What | Why it is first |
|---|---|---|
| `00-namespace.yaml` | namespace `triage`, Pod Security `restricted` **enforced** | every later object lives in it |
| `20-rbac-analysis-jobs.yaml` | two ServiceAccounts, a Role of three verbs, its binding | the Platform cannot submit without it |
| `30-networkpolicy-analysis.yaml` | the sandbox's egress | apply *before* the first Job, not after |
| `50-secret-analysis.example.yaml` | the shape of the Job's Secret — **an example, no values** | the real one is created out of band |
| `postgres/analysis-writer-role.sql` | the role the Job answers with | `psql -f`, as the schema owner |
| `40-job-analysis-template.yaml` | the Job itself | Triage builds this in Python; the file is the reviewable form |
| `41-job-egress-probe.yaml` | curl, once per destination, from inside the sandbox | how 4.4 is answered: by trying |

`optional/runtimeclass-gvisor.yaml` is **not applied** and is not in that order. gVisor was
chosen for a Job that ran an agent with tool use; ADR-0014 removed the agent and ADR-0024
draws the consequence — what is left clones with `git`, reads files and makes one HTTPS
call, and never executes what it reads. It is kept because the two conditions that bring a
kernel boundary back are written down, and the day one is met this is `runtime_class:
gvisor` in `config.yaml` plus `runsc` on the nodes, not a rewrite.

A RuntimeClass applied without that node work is worse than none: every analysis Job stays
Pending on a handler no node installs, which is the failure the manifest was there to
prevent.

## Two places the plan's wording is wider than the truth

**"NetworkPolicy with egress to GitHub and the registry only."** The registry is not the
Pod's traffic. Image pulls are the kubelet's, and no NetworkPolicy governs them — granting a
Pod egress to the registry would be granting something nothing uses. What the Job does need,
beyond GitHub, is DNS, the in-cluster LiteLLM proxy (the analysis is one `analysis`-tier
call, ADR-0014) and PostgreSQL (its only way to answer is one row, ADR-0009). Those four,
and nothing else — not Datadog, not Slack, not Jira, not the instance metadata service.

**"The insert-only role the Job writes its result with" (ADR-0009).** The code is not
insert-only: `SqlRepository.save_analysis_result` reads the row back by job name before
writing it, so the role holds `SELECT, INSERT, UPDATE` on `triage.analysis_results` and
nothing else. Still one table, still no path to signals, diagnoses or tickets — but the ADR
says something narrower than what is granted, and the SQL file says so where it grants it.

## What a first cluster would settle

- **4.2** — `KubernetesJobApi` has never spoken to an API server. Submitting one Job through
  it and reading the row back is the whole of it.
- **4.4** — `41-job-egress-probe.yaml`, applied, prints one line per destination and exits
  non-zero if any of them disagrees with what was granted. That is the verification; it has
  not been run.

Both need the same thing: a namespace on a cluster, credentials that can apply this
directory, and a Postgres reachable from it. No special node pool — that requirement left
with gVisor (ADR-0024).
