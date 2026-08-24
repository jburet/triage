# 0021 — Where a workload is defined in its IaC repository is declared, not inferred

Status: Proposed. Demotes M6 3.1's naming rule from the rule to the fallback.

## Decision

An IaC repository may **declare** which of its paths define each workload it provisions,
keyed by the repository that workload runs:

```yaml
- url: github.com/zeenea/platform-infra
  kind: terraform
  defines:
    platform: ["terraform/eks_module/*"]
```

Where a declaration exists it is the whole selection, and a declaration matching nothing
answers nothing — never the naming rule's answer instead. A workload with no declaration
still gets the naming rule of M6 3.1: a directory that is the repository's name or ends in
it, `zeenea-platform` for `platform`.

## Why

M6 3.1 resolves the paths that define a workload from the IaC repository's own file
listing, by matching path *segments* against the workload's name. On 2026-08-24 the first
live pass ran that rule against the real `platform-infra` and found nothing, and the
report said so: *"platform-infra was resolved, the chart inside it was not"*.

The repository was right. The file it was looking for is
`terraform/eks_module/eks.tf` — `resource "kubernetes_stateful_set_v1" "platform"`, and
directly beneath it the liveness, readiness and startup probes whose timeouts the
2026-08-22 incident turned on. The path's segments are `terraform`, `eks_module`, `eks`.
The workload's name is the **resource label inside the file**, one level below any path,
and no rule over path segments can reach it.

Nor is that repository unusual. It organises by what it provisions *on* — `core-eks`,
`database`, `storage`, `jobs` — which is how infrastructure repositories are normally laid
out, and the naming rule's premise (a directory per service) describes charts repositories,
not Terraform ones. The rule was written from a tree nobody had looked at; the fixture it
was tested against was invented in that tree's image.

Two alternatives were weighed:

- **Search the repository's contents for the resource label.** It needs no declaration and
  survives the module being renamed. Rejected: GitHub code search is a ranked result on a
  separate 10-requests-per-minute limit, and M6's premise ([ADR-0019](0019-workload-mapping-from-the-running-image.md),
  [ADR-0020](0020-a-commit-nothing-observed-is-never-the-deployed-one.md)) is that a
  location is observed or explicitly unknown, never inferred. A ranked guess at which file
  defines a workload is exactly the confident-and-wrong answer those two refuse.
- **Read the `.tf` files and parse them.** Correct, and 40 blob reads per repository per
  pass to re-derive a fact that changes when someone renames a module — which is to say,
  about annually.

A declaration is deterministic, reviewed as a diff, and is the third of its kind:
`image_name` and `tag_template` were both added when a rule could not know something a
person could state in one line. Nothing here is discoverable from the cluster, and the
cost of being wrong is an analysis that reads the wrong files and says nothing about
having done so.

A stale declaration is what this trades for. It fails loudly — no paths, a warning naming
the repository and the pattern, and the pass's own "mapped, chart not found" line — which
is the failure mode M6 was built to have.

## Consequences

- `Repo.defines` in `config.yaml`; `workload_paths(..., declares=…)` selects by glob over
  the whole path when it is non-empty.
- Where no declaration exists nothing changes, so the charts-repository case M6 3.1 was
  written for still works and its tests still hold.
- The declaration is per workload, so one IaC repository provisioning several workloads
  says which paths belong to which — the case the naming rule got right by accident and
  this one gets right on purpose.

## What this does not see

- A module renamed after the declaration was written. The pass reports the workload as
  mapped-but-undefined, which is where that shows up.
- Which *part* of the declared module concerns this workload. Sixty tenants share
  `terraform/eks_module/`; the per-tenant values are in `terraform/conf/<region>/<env>/`,
  and nothing yet resolves a tenant to its own tfvars.

## Revisit when

F0's cartography summarises IaC repositories. It reads the files, so it can record which
resources each path declares — at which point the declaration becomes a fallback for
repositories F0 has not yet summarised, and this ADR is superseded rather than edited.
