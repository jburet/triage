"""Merging summaries into the map, and writing it down (architecture §2.5).

The merge is a rule, not a model call. Everything it joins is already
structured — a summary names the service it deploys as, a module names the
services it provisions for, config.yaml names the owner — so a model here would
add spend, latency and a way to be wrong to a lookup that is none of those. The
one genuinely fuzzy join, a resource's free-text ``serves``, is matched by name
and left empty when it does not match, because an empty list of resources is
recoverable and a wrong one sends a developer to the wrong Terraform file.
"""

from langchain_core.runnables import RunnableConfig

from triage.db.repo import SystemMapEntry
from triage.graphs.state import CartographyState, Summarised
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.common import Unknown
from triage.schemas.common import render as render_field
from triage.schemas.system_map import (
    ModuleMapping,
    RepoSummary,
    ServiceEntry,
    SystemMap,
    SystemMapKind,
    TerraformModuleEntry,
    TerraformResource,
    TerraformSummary,
)


def _modules(summary: TerraformSummary) -> list[ModuleMapping]:
    return [] if isinstance(summary.modules, Unknown) else list(summary.modules)


def _resources(summary: TerraformSummary) -> list[TerraformResource]:
    return [] if isinstance(summary.resources, Unknown) else list(summary.resources)


def _module_resources(
    module: ModuleMapping, resources: list[TerraformResource]
) -> list[TerraformResource]:
    basename = module.module.rstrip("/").split("/")[-1]
    return [resource for resource in resources if basename in resource.address.split(".")]


def _serves(resource: TerraformResource, service: str) -> bool:
    return service.lower() in render_field(resource.serves).lower()


def _resources_for_service(service: str, terraform: list[Summarised]) -> list[TerraformResource]:
    """Resources a service owns: named by the resource, or by the module that declares it."""
    found: dict[str, TerraformResource] = {}
    for item in terraform:
        summary = item.summary
        assert isinstance(summary, TerraformSummary)
        resources = _resources(summary)
        for resource in resources:
            if _serves(resource, service):
                found[resource.address] = resource
        for module in _modules(summary):
            if isinstance(module.services, Unknown) or service not in module.services:
                continue
            for resource in _module_resources(module, resources):
                found[resource.address] = resource
    return list(found.values())


def _split(summaries: list[Summarised]) -> tuple[list[Summarised], list[Summarised]]:
    apps = [item for item in summaries if isinstance(item.summary, RepoSummary)]
    terraform = [item for item in summaries if isinstance(item.summary, TerraformSummary)]
    return apps, terraform


async def _carried_resources(deps: Deps, service: str) -> list[TerraformResource]:
    """What the map already knows, for a run that summarised no Terraform at all.

    An incremental run touching one application repository must not blank the
    infrastructure links a full pass established (ADR-0006).
    """
    existing = await deps.repo.system_map_for_service(service)
    return list(existing.terraform_resources) if existing else []


async def build_system_map(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """Merge this run's summaries with the ownership config.yaml declares."""
    deps = deps_from_runnable_config(config)
    apps, terraform = _split(state.get("summaries", []))

    services: list[ServiceEntry] = []
    unowned: list[str] = []
    for item in apps:
        summary = item.summary
        assert isinstance(summary, RepoSummary)
        resources = (
            _resources_for_service(summary.service, terraform)
            if terraform
            else await _carried_resources(deps, summary.service)
        )
        services.append(
            ServiceEntry(
                name=summary.service,
                repo_url=item.target.url,
                team=item.target.team,
                source_commit=item.target.commit,
                summary=summary,
                terraform_resources=resources,
            )
        )
        if item.target.team is None:
            unowned.append(summary.service)

    modules: list[TerraformModuleEntry] = []
    for item in terraform:
        summary = item.summary
        assert isinstance(summary, TerraformSummary)
        resources = _resources(summary)
        for module in _modules(summary):
            modules.append(
                TerraformModuleEntry(
                    name=module.module,
                    repo_url=item.target.url,
                    team=item.target.team,
                    source_commit=item.target.commit,
                    mapping=module,
                    resources=_module_resources(module, resources),
                )
            )
        if item.target.team is None:
            unowned.append(item.target.url)

    return {
        "system_map": SystemMap(services=services, terraform_modules=modules),
        "unowned": unowned,
    }


async def persist_map(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """Write one row per service and per Terraform module, keyed by ``(kind, name)``."""
    deps = deps_from_runnable_config(config)
    system_map = state.get("system_map") or SystemMap()

    entries = [
        SystemMapEntry(
            kind=SystemMapKind.SERVICE,
            name=service.name,
            team=service.team,
            source_commit=service.source_commit,
            payload=service.model_dump(mode="json"),
        )
        for service in system_map.services
    ] + [
        SystemMapEntry(
            kind=SystemMapKind.TERRAFORM_MODULE,
            name=module.name,
            team=module.team,
            source_commit=module.source_commit,
            payload=module.model_dump(mode="json"),
        )
        for module in system_map.terraform_modules
    ]

    written = await deps.repo.upsert_system_map_entries(entries)
    return {"entries_written": written}


async def carry_forward(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """Move the recorded commit on rows this run judged still accurate (ADR-0015).

    A no-op for every run that summarised everything it was asked to, which is
    why it sits on the main path rather than behind a branch: the alternative is
    a route that has to reason about a run doing both at once.
    """
    deps = deps_from_runnable_config(config)
    carried = state.get("carried_forward", [])
    for entry in carried:
        await deps.repo.advance_source_commit(entry.repo_url, entry.commit)
    return {}


async def notify_platform(
    state: CartographyState, config: RunnableConfig | None = None
) -> CartographyState:
    """One notice per run about what the map is missing, to the team that owns F0.

    Unattributed services and unsummarised repositories are the same problem seen
    twice — the map is less complete than it looks — and the SRE team fixes both
    in config.yaml. One message per run rather than one per repository, so a
    broken repository does not drown the channel it is reported in.
    """
    deps = deps_from_runnable_config(config)
    unowned = state.get("unowned", [])
    failures = state.get("failures", [])

    lines = [":world_map: Cartography run finished with gaps."]
    if unowned:
        lines.append(
            "*No team declared in config.yaml* (recorded with no owner): "
            + ", ".join(f"`{name}`" for name in unowned)
        )
    if failures:
        lines.append("*Not summarised:*")
        lines.extend(f"• `{failure.repo_url}` — {failure.reason}" for failure in failures)

    await deps.slack.post(channel=deps.config.platform_channel(), text="\n".join(lines))
    return {}
