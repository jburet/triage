# 0024 — No kernel boundary for a Job that executes nothing

Status: Accepted. Amends the architecture's gVisor line and completes ADR-0014.

## Decision

The analysis Job runs under the node's own container runtime. `runtimeClassName` is omitted
from the manifest, `config.analysis.job.runtime_class` defaults to empty, and
`deploy/optional/runtimeclass-gvisor.yaml` is kept out of the applied set.

Everything else about the sandbox stays exactly as it is: the `restricted` namespace, the
non-root read-only-rootfs container with every capability dropped, the NetworkPolicy, the
ServiceAccount with no token, the one-table Postgres grant, the resource ceiling.

## Why

gVisor was chosen for a different Job. The architecture's line was "Claude Agent SDK in a
gVisor Job", and a kernel boundary is the right answer for an open-ended agent with tool use
executing inside the sandbox. ADR-0014 removed the agent — "a bounded context gather
followed by a single structured `analysis`-tier call, no agentic loop, no tool use inside
the sandbox" — and noted the consequence in one line ("a smaller sandbox") without asking
whether the runtime should change too. This is that question, asked.

What the Job does today, in full: one `git` subprocess to clone at a pinned object name,
file reads, one HTTPS call to the LiteLLM proxy, one row to Postgres. **It never executes
the code it analyses.** There is no build, no test run, no dependency resolution — nothing
that turns a repository's contents into instructions for a CPU. gVisor intercepts syscalls
made by untrusted code; here the untrusted input is text that gets *read*, and the reader is
our own bounded program.

The threat that is real is exfiltration, not escape. The Job holds a GitHub token and a
model key and sees source. That is answered by the NetworkPolicy — DNS, GitHub, the
in-cluster proxy, Postgres, and nothing else — and by the Secret's shape, neither of which
gVisor contributes to. Prompt injection through a source file is also real, and gVisor is
irrelevant to it: the answer is that the model's output is a validated schema nothing
executes.

Against a threat requiring code execution that does not occur, the cost is not small: `runsc`
on the nodes is a containerd shim and a config drop-in, which on EKS is node bootstrap in
`platform-infra`'s Terraform, plus a labelled and tainted node pool, plus syscall-compat
risk on a `git`-heavy workload, plus the standing failure mode of a RuntimeClass whose nodes
were never prepared — every Job Pending, forever.

Two things this is **not** an argument for. It is not an argument that the sandbox is
unnecessary: the controls above are what contain a token and a network path, and they are
cheaper and more relevant than the runtime. And it is not an argument about blast radius —
if the concern is that an analysis Job shares a cluster with production tenants, the lever is
a node pool or a cluster of its own, not a different runtime for a process that only reads.

## Revisit when

Either of these becomes true, and each is plausible:

- **The analysis executes the repository.** Building it, running its tests, resolving its
  dependencies — anything that reproduces a failure rather than reading about it. Then the
  cloned tree is running code and the kernel boundary is the right control again.
- **An investigative kind takes ADR-0014's opening.** That ADR says the investigative kinds
  "are free to make the opposite choice" and use an agent. An agent with tool use inside the
  Job restores exactly the design gVisor was picked for.

The reinstatement is `runtime_class: gvisor` in `config.yaml`, applying
`deploy/optional/runtimeclass-gvisor.yaml`, and the node work in `platform-infra` — in that
order, because the last one is the one that takes weeks.
