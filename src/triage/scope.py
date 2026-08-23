"""Who owns an alert, and whether Triage should look at it at all (ADR-0017).

Matched by pattern, never by enumeration. One StatefulSet monitor fired for 66
distinct groups in 40 days and those groups are per-customer tenants —
``plt-merck``, ``plt-hcl-software-uat`` — so a list of services in ``config.yaml``
would be obsolete the day a customer is provisioned.

The resolution ladder is the ``service:`` tag, then a team's service patterns,
then ``kube_namespace`` / ``kube_stateful_set``. Environment is *never* taken
from an ``env:`` tag on the alert, because no alert carries a usable one: for
Kubernetes monitors it is inside ``kube_cluster_name``. An unmapped cluster is
out of scope with that as the reason, and is never assumed to be production —
which is the whole point of a map rather than a guess.

A monitor that groups only ``by service`` carries no cluster at all, and the
first live run showed what that costs: the pod-down monitor — the one this whole
feature exists for — resolved to no environment and every one of its alerts would
have been dropped. Its *query* filters ``env:prod``, so the environment is read
from there when there is no cluster. That is not the guess the ADR forbids: it is
the monitor's own filter, stated by whoever wrote it. The monitor's *name* is not
read, though ADR-0017 mentions it — "prod-like preprod" contains "prod", and a
rule that cannot tell those apart is the guess again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch

from triage.config import Config, RepoKind, Team
from triage.db.repo import TriageRepository
from triage.schemas.alert import Alert

ENV_FILTER = re.compile(r"\benv:([\w.-]+)")


def declared_environment(alert: Alert) -> str | None:
    """The environment the monitor's own query filters on, if it filters on one."""
    match = ENV_FILTER.search(alert.monitor_query or "")
    value = match.group(1) if match else None
    return value if value and "*" not in value else None


@dataclass(frozen=True)
class Routing:
    """Where an alert goes, or why it goes nowhere."""

    in_scope: bool
    team: str | None
    environment: str | None
    service: str | None
    reason: str


def _matches(team: Team, alert: Alert) -> bool:
    scope = alert.scope
    if scope.service and any(fnmatch(scope.service, p) for p in team.service_patterns):
        return True
    for name in (scope.stateful_set, scope.namespace):
        if name and any(fnmatch(name, p) for p in team.service_patterns):
            return True
        if name and any(fnmatch(name, p) for p in team.namespace_patterns):
            return True
    return False


def resolve(config: Config, alert: Alert) -> Routing:
    """Match one alert to a team and an environment, or refuse it with a reason."""
    scope = alert.scope
    service = scope.workload
    team = next((team for team in config.teams if _matches(team, alert)), None)
    if team is None:
        return Routing(
            in_scope=False,
            team=None,
            environment=None,
            service=service,
            reason=f"no team's patterns match {
                service or 'an alert with no service, namespace or stateful set'
            }",
        )

    environment = config.environment_of(scope.cluster) or declared_environment(alert)
    if environment is None:
        return Routing(
            in_scope=False,
            team=team.name,
            environment=None,
            service=service,
            reason=(
                f"cluster {scope.cluster!r} is not in the clusters map, so its environment "
                f"is unknown — and an unknown environment is not assumed to be production"
                if scope.cluster
                else "the alert carries no cluster and its monitor's query filters on no "
                "environment, so the environment cannot be determined"
            ),
        )
    if environment not in team.environments:
        return Routing(
            in_scope=False,
            team=team.name,
            environment=environment,
            service=service,
            reason=f"{environment} is not an environment {team.name} has Triage watch "
            f"({', '.join(team.environments) or 'none configured'})",
        )
    return Routing(
        in_scope=True,
        team=team.name,
        environment=environment,
        service=service,
        reason=f"{service} in {environment} is owned by {team.name}"
        + ("" if config.environment_of(scope.cluster) else ", from the monitor's own env: filter"),
    )


async def deployed_repo(
    config: Config,
    repository: TriageRepository,
    service: str,
    kind: RepoKind = RepoKind.APPLICATION,
) -> tuple[str | None, str | None]:
    """The repository and commit to read this service in, and where they came from.

    The derived workload first (M6): it is keyed on the name the cluster uses, so
    it is the only one of the three that can answer for a customer's instance of
    the mono-tenant platform, and its commit is what that instance was actually
    running. Then F0's map, which is keyed on the name a repository says it
    deploys as. Then config.yaml's ``serves`` patterns.

    Where the commit is unknown — a workload whose image tag carried none, a
    pattern match, a map entry — it falls back to the last commit F0 summarised
    for that repository: a fact about the repository, not a claim about which
    build this tenant runs, and the diagnosis says so.
    """
    if kind is RepoKind.APPLICATION:
        workload = await repository.workload_for_service(service)
        if workload is not None and workload.repo_url is not None:
            commit = workload.deployed_commit if isinstance(workload.deployed_commit, str) else None
            return workload.repo_url, commit or await repository.last_summarised_commit(
                workload.repo_url
            )

    entry = await repository.system_map_for_service(service)
    if entry is not None:
        return entry.repo_url, entry.source_commit
    declared = config.repo_serving(service, kind)
    if declared is None:
        return None, None
    return declared.url, await repository.last_summarised_commit(declared.url)
