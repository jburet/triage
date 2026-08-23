# Plan: M6 — the service map, from workload to repository (2026-08-23)

Architecture §5 (F0); ADR-0006, ADR-0015. Depends on M2 (cartography) and M3 Phase 1
(the Analysis sub-graph, which is what consumes the answer). Independent of M4.

## Why this exists

F0 keys the system map on **the name a repository says it deploys as**, which is right for
the thirteen multi-tenant applications and wrong for the one that matters most. `platform`
is mono-tenant — one Kubernetes StatefulSet per customer — so what alerts, and what Datadog
tags, is `plt-merck-qa`, `plt-hcl-software-uat`, one name per tenant, none of which any
repository claims. Every alert from the pod-down monitor therefore resolved to *"service is
not in the system map"*, and the analyses that give Triage its reason to exist never ran.

M3 shipped `Repo.serves` patterns as the stopgap: `serves: ["plt-*"]` in `config.yaml`, one
resolution rule shared by `qualify` and `investigate`. It works, and it is a hand-maintained
list of guesses about naming. This milestone replaces the guess with a derivation.

The live run on 2026-08-23 also showed the second half of the problem. With the mapping
supplied by hand, three analyses did run — and all three answered `Unknown`, because the
Terraform selection profile reads `*.tf` and the probe timeouts and memory limits that would
have explained the incident live in `helm/zeenea-platform/values.yaml`. Resolving *which
repository* is only half a mapping; the other half is **where in it the workload is defined**.

## The seed: the architecture document

[`docs/reference-aws-architecture-2026-04-20.md`](../reference-aws-architecture-2026-04-20.md),
copied into the repository so the plan does not depend on a file in someone's Downloads,
is the authority for the part of this that is
not discoverable from a cluster. Its repository map gives, for twenty repositories:

| Column | What it settles |
|---|---|
| Repository | the code repository, by name |
| Tenancy model | `Mono-tenant (StatefulSet per tenant)` vs `Multi-tenant (shared pod)` — which decides whether a service name can be a tenant name at all |
| Deployment method | `EKS StatefulSet via platform-infra` vs `EKS via application-deployer` — which IaC repository provisions it, and therefore where an `iac_analysis` must read |

Three facts from it are load-bearing and are not in any cluster: that `platform` is the only
mono-tenant workload, that `platform-infra` provisions it per tenant via Terraform workspaces,
and that everything else is deployed by the shared `application-deployer`.

What the document does **not** contain, and must not be invented from it: the `plt-` prefix,
any tenant or namespace name, and the path of a Helm chart inside a repository. It is a seed,
not the map.

## The derivation

The running workload already names its own repository, and Triage already collects it. Every
container start event in the incident collection carries the image — `short_image:platform`,
`platform@sha256:2e15f697…` — and every application image is built into the infra account's
ECR under the repository's own name. So:

1. **Image → repository.** The image name in the workload's own change events resolves the
   application repository, for any service, without a pattern.
2. **Digest → commit.** The image digest is what was deployed; the tag or an ECR image
   annotation carries the commit. This is the fact M3 could not get — today the analysis is
   run at "the last commit F0 summarised", which is a fact about the repository rather than
   about this tenant, and every diagnosis has to say so.
3. **Repository → IaC repository and workload path**, from the seed document plus one
   discovery pass over the IaC repository for the chart or module that defines the workload.
4. **Patterns last.** `serves` remains, as the fallback for a workload that has emitted no
   image event in the retention window.

## Public interface

- `triage.schemas.system_map` gains `WorkloadEntry`: `service`, `repo_url`, `image`,
  `image_digest`, `deployed_commit: MaybeUnknown`, `iac_repo_url`, `iac_paths: list[str]`,
  `tenancy: Tenancy`, `source: MappingSource` (`image` | `seed` | `pattern` | `manual`).
- `TriageRepository` gains `upsert_workload(entry)`, `workload_for_service(service)`.
- `triage.scope.deployed_repo` keeps its signature and consults workloads before the map and
  the patterns — every caller (`qualify`, `investigate`) is unchanged.
- `triage.mapping.seed`: parses the architecture document's repository table into
  `SeedEntry` rows. A parse that finds fewer rows than the file's table has is an error, not
  a partial import.
- `triage.graphs.mapping`: graph `service_mapping`, registered in `langgraph.json`. Input: a
  service name, or nothing for a full pass. No model call in the resolution path.
- `scripts/run_mapping.py`, mirroring `run_cartography.py`.

## Phase 1: the seed

- [x] 1.1 The architecture document parses into one `SeedEntry` per repository row, carrying
      tenancy and deployment method; a row whose tenancy or deployment cell is unrecognised is
      reported by name rather than defaulted.
- [x] 1.2 The seed is versioned in the repository as data (`config/repository-map.yaml`),
      generated from the document by a script and reviewed as a diff — the document is
      someone's Markdown and will be edited without telling us.
- [x] 1.3 A repository in the seed that is not declared in `config.yaml` is listed in the
      mapping report as unclaimed, with its team unknown; nothing is invented for it.
- [x] 1.4 `Tenancy.MONO_TENANT` on a repository is what allows a service name to differ from
      the repository's declared service; for a multi-tenant repository the two must match or
      the mapping is reported as a conflict.

## Phase 2: derivation from the cluster

- [x] 2.1 A container-start or StatefulSet change event carrying an image resolves the
      application repository by image name, and the resulting `WorkloadEntry` records
      `source = image` and the digest it saw.
- [x] 2.2 An image name that matches no repository in the seed is a stated failure naming the
      image, never a fuzzy match onto the nearest repository name.
- [x] 2.3 The deployed commit is resolved from the image (tag or ECR image metadata) and
      recorded as `Filled`; when it cannot be, `deployed_commit` is an `Unknown` whose reason
      says the image was found but its commit was not — the two are different failures and
      the diagnosis reads differently for each.
- [ ] 2.4 A service with no image event inside the collection window falls back to the seed's
      tenancy plus `config.yaml` patterns, recording `source = pattern`; with neither, the
      mapping is absent and the caller says "not mapped", as it does today.
- [ ] 2.5 Re-running the derivation for a service whose image digest is unchanged rewrites
      nothing and says so, on the same reasoning as ADR-0015.

## Phase 3: where the workload is defined

- [ ] 3.1 For each mapped service, the IaC repository from the seed is searched once for the
      files that define *this* workload — Helm chart, values file, Terraform module — and the
      paths are stored on the entry as `iac_paths`.
- [ ] 3.2 `iac_analysis` gathers from `iac_paths` first and the profile's globs second, so a
      probe timeout in `helm/zeenea-platform/values.yaml` is read; the run on 2026-08-23
      returned `Unknown` three times for exactly this reason.
- [ ] 3.3 The Terraform selection profile includes Helm and Kubernetes manifests
      (`*.yaml` under a chart directory, `values*.yaml`, `templates/*.yaml`), and the
      application profile is unchanged — an infrastructure question is answered from
      infrastructure files wherever they live, not from files with a `.tf` suffix.
- [ ] 3.4 A per-tenant value that overrides the chart default (the 40+ per-tenant parameters,
      the three performance profiles) is reported as an unknown when it cannot be read, rather
      than the chart default being quoted as this tenant's value.

## Phase 4: use and reporting

- [ ] 4.1 `deployed_repo` prefers a `WorkloadEntry`, then the system map, then patterns, and
      the `Hypothesis` records which of the three answered — a diagnosis built on a pattern
      guess must not read like one built on the image that was running.
- [ ] 4.2 A diagnosis whose commit came from `source = pattern` states in
      `confidence_rationale` that the commit is the repository's last summarised one and not
      this tenant's deployed one, and cannot be `high` on that basis alone.
- [ ] 4.3 One mapping pass over every service seen in the last N days produces a report:
      mapped by image, mapped by pattern, unmapped, conflicting. Posted to the platform
      channel, since an unmapped production workload is Triage's own gap.
- [ ] 4.4 `make run-mapping` prints the same report locally against the real cluster
      telemetry, read-only.

## What this does not deliver

- No discovery of *new* services: the seed and the alerts decide what exists. A service that
  never alerts and is not in the document is invisible, deliberately — enumerating an AWS
  account is a different feature with different credentials.
- No ECR read if the image tag already carries the commit; 2.3 is allowed to be satisfied by
  the tag alone.
- No change to F0's repository summaries, which stay keyed as they are.

## Open risks

- **The document is a snapshot.** Dated 2026-04-20 and hand-written; a repository added since
  is missing and a tenancy model changed since is wrong. 1.2's generated file makes the drift
  visible as a diff, which is the most that can be done from here — the alternative, deriving
  tenancy from the cluster, is 2.1 and only covers what alerts.
- **Image name equals repository name is a convention too.** It holds for `platform` and for
  every image observed on 2026-08-23, and it is exactly the kind of thing that holds until an
  image is renamed. 2.2 fails loudly rather than guessing, which turns a silent
  misattribution into a mapping report line.
- **The commit may not be in the image at all.** If tags are `latest` or a build number with
  no commit anywhere in ECR metadata, 2.3 degrades to Unknown for every service and the
  analyses keep running at F0's last summarised commit — the improvement then is only the
  honesty of 4.2, not the accuracy of the analysis.
