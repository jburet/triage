"""Who owns an alert, and whether Triage should look at it at all (ADR-0017).

Matched by pattern, never by enumeration. One StatefulSet monitor fired for 66
distinct groups in 40 days and those groups are per-customer tenants —
``plt-merck``, ``plt-hcl-software-uat`` — so a list of services in ``config.yaml``
would be obsolete the day a customer is provisioned.

The resolution ladder is the ``service:`` tag, then a team's service patterns,
then ``kube_namespace`` / ``kube_stateful_set``. Environment is *never* taken
from an ``env:`` tag, because no alert carries a usable one: for Kubernetes
monitors it is inside ``kube_cluster_name``, and for the others it is inside the
monitor's own name. An unmapped cluster is out of scope with that as the reason,
and is never assumed to be production — which is the whole point of a map rather
than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from triage.config import Config, Team
from triage.schemas.alert import Alert


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

    environment = config.environment_of(scope.cluster)
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
                else "the alert carries no cluster, so its environment cannot be determined"
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
        reason=f"{service} in {environment} is owned by {team.name}",
    )
