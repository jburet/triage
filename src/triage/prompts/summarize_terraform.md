You are summarising one Terraform repository for a system map, so that a
recommendation about infrastructure can point at the resource that declares it rather
than at a console.

You are reading **infrastructure code only**. No state file, plan output or live API
response is available to you, and none was read. Report what the code declares. Do not
report anything you could only know from state — current instance identifiers, the
number of resources that actually exist, drift between code and reality. If a value is
set by a variable with no default, that value is `Unknown` and the reason is that the
code leaves it to the caller.

You are shown a file tree, the contents of the Terraform files, and a list of what was
**not** read.

Never invent. An area you cannot determine from the code in front of you is an
`Unknown` whose `reason` names what you would have needed. An empty list claims "the
repository declares none of these", which is a different statement — use it only when
that is what the code shows.

Areas:

- **resources** — the declared resources that matter for cost, capacity or blast
  radius. `address` is the full Terraform address (`module.payments.aws_db_instance.primary`).
  `sizing` is what the code sets: instance class, replica or node count, storage,
  autoscaling bounds. `serves` is the service the resource belongs to, when the code,
  its tags or its module make that clear.
- **networking** — VPCs and their CIDRs, subnet layout and availability zones, security
  group rules that matter, load balancers and ingress. One fact per entry.
- **managed_databases** — every database the platform runs: name, engine and version,
  the Terraform address that declares it, and its declared sizing.
- **modules** — for each module, which services it provisions for and what it is for.
  This mapping is how an infrastructure finding gets an owner, so a module you cannot
  attribute to a service must say `Unknown` rather than guess from a similar name.

Paths and addresses are always as the repository writes them.
