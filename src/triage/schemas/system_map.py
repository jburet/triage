"""What F0 discovers about the system (roadmap F0, architecture §2.5).

Every other feature locates code, infrastructure and owners through this map, so
a summary that quietly guesses is worse than one that admits a gap. Each area is
either a non-empty answer or an :class:`~triage.schemas.common.Unknown` carrying
the reason it could not be filled — and an empty list is rejected for the same
reason a placeholder string is: "this service exposes no HTTP endpoints" and "I
could not find the endpoints" are different facts, and only one of them should
be acted on.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, TypeAlias, TypeVar

from pydantic import AfterValidator, BaseModel, Field

from triage.schemas.common import Filled, MaybeUnknown, Unknown


def reject_empty[T](value: list[T]) -> list[T]:
    """An empty list asserts nothing, so it may not stand in for an answer."""
    if not value:
        raise ValueError(
            "an empty list says nothing. Either list what was found, or use an Unknown "
            "whose reason states why nothing was found."
        )
    return value


_T = TypeVar("_T")

Listed: TypeAlias = Annotated[list[_T], AfterValidator(reject_empty)]
"""A list that has been checked to contain something."""


class EntryPointKind(StrEnum):
    HTTP_SERVER = "http_server"
    CONSUMER = "consumer"
    SCHEDULED_JOB = "scheduled_job"
    CLI = "cli"
    WORKER = "worker"
    OTHER = "other"


class EntryPoint(BaseModel):
    """Where execution starts. The first place a developer opens."""

    kind: EntryPointKind
    name: Filled
    path: Filled = Field(description="File or module it lives in, relative to the repo root.")


class Endpoint(BaseModel):
    """One externally reachable route."""

    method: str = Field(description="HTTP method, or the verb of whatever protocol serves it.")
    path: Filled
    handler: MaybeUnknown = Field(description="Function or class that serves it, with its file.")


class DependencyKind(StrEnum):
    HTTP = "http"
    GRPC = "grpc"
    QUEUE = "queue"
    DATABASE = "database"
    CACHE = "cache"
    OTHER = "other"


class ServiceDependency(BaseModel):
    """An outbound call this repository makes. Half of the blast-radius question."""

    target: Filled = Field(description="Service, queue or datastore called.")
    kind: DependencyKind
    seen_at: Filled = Field(description="Where in the code the call is made.")


class DatabaseAccess(BaseModel):
    """How this repository reaches one datastore. F3 traces slow queries back through it."""

    datastore: Filled
    access: Filled = Field(description="ORM, raw SQL, migrations — how queries are issued.")
    tables: list[str] = Field(default_factory=list)


class Observability(BaseModel):
    """What the service already emits: what F1 can collect, and what the alert audit judges."""

    metrics: MaybeUnknown
    logging: MaybeUnknown
    tracing: MaybeUnknown
    dashboards: list[str] = Field(default_factory=list)


class RepoSummary(BaseModel):
    """Structured summary of one application repository (roadmap F0)."""

    repo_url: str
    # Bounded because it is a key: the system map is looked up by it, and the
    # database column is 256 characters. Asked for it unbounded, a model answered
    # `platform (deploys as "zeenea-platform"; from helm chart … but build.sbt
    # names the sbt root project …)` — a correct sentence in a field that has to
    # be a name.
    service: Filled = Field(
        max_length=128,
        description=(
            "The service name this repository deploys as, and nothing else — an "
            "identifier, not a sentence. Where it comes from belongs in the areas "
            "below, not here."
        ),
    )
    languages: Listed[Filled] | Unknown
    frameworks: Listed[Filled] | Unknown
    entry_points: Listed[EntryPoint] | Unknown
    endpoints: Listed[Endpoint] | Unknown
    depends_on: Listed[ServiceDependency] | Unknown
    database_access: Listed[DatabaseAccess] | Unknown
    observability: Observability | Unknown


class TerraformResource(BaseModel):
    """One declared resource, with what sets its cost and capacity."""

    address: Filled = Field(
        description="Full Terraform address, e.g. module.payments.aws_db_instance.primary."
    )
    type: Filled
    sizing: MaybeUnknown = Field(description="Instance class, replica count, storage.")
    serves: MaybeUnknown = Field(description="Service this resource belongs to.")


class ManagedDatabase(BaseModel):
    """A database the platform runs. F3 points its recommendations at the resource, not the DB."""

    name: Filled
    engine: MaybeUnknown
    address: Filled = Field(description="Terraform address of the resource that declares it.")
    sizing: MaybeUnknown


class NetworkFact(BaseModel):
    """One networking arrangement: a VPC, a subnet layout, a load balancer, an ingress rule."""

    subject: Filled
    detail: Filled


class ModuleMapping(BaseModel):
    """Which services a module provisions for — the join that turns infra into an owner."""

    module: Filled = Field(description="Module path or name within the Terraform repository.")
    services: Listed[Filled] | Unknown
    purpose: Filled


class TerraformSummary(BaseModel):
    """Structured summary of one Terraform repository, from code only — no state is read."""

    repo_url: str
    resources: Listed[TerraformResource] | Unknown
    networking: Listed[NetworkFact] | Unknown
    managed_databases: Listed[ManagedDatabase] | Unknown
    modules: Listed[ModuleMapping] | Unknown


class Tenancy(StrEnum):
    """How many customers one deployment of a repository serves.

    The vocabulary is the architecture document's own, so that a cell nobody has
    taught this enum about is an unrecognised row rather than a row quietly
    filed under the nearest value (M6 1.1). ``MONO_TENANT`` is the load-bearing
    one: it is what allows a running service to be named after a customer rather
    than after the repository it runs.
    """

    MONO_TENANT = "mono_tenant"
    MULTI_TENANT = "multi_tenant"
    PER_TENANT_PROVISIONING = "per_tenant_provisioning"
    NOT_APPLICABLE = "not_applicable"


class Deployer(StrEnum):
    """What puts a repository into a cluster, and therefore where its IaC lives."""

    APPLICATION_DEPLOYER = "application_deployer"
    PLATFORM_INFRA = "platform_infra"
    GITHUB_ACTIONS = "github_actions"
    AWS_ORGANIZATION = "aws_organization"
    NOT_DEPLOYED = "not_deployed"
    """The deployer itself, and anything else nothing deploys."""


IAC_REPO: dict[Deployer, str] = {
    Deployer.APPLICATION_DEPLOYER: "application-deployer",
    Deployer.PLATFORM_INFRA: "platform-infra",
}
"""The two deployers that are themselves repositories an ``iac_analysis`` can read."""


class SeedEntry(BaseModel):
    """One row of the architecture document's repository map.

    The seed answers what no cluster can: whether a repository is mono-tenant,
    and which IaC repository provisions it. It is not the map — it names no
    tenant, no namespace and no chart path.
    """

    repository: str
    role: str
    tenancy: Tenancy
    deployment: Deployer

    @property
    def iac_repo(self) -> str | None:
        """The repository whose code defines this workload's infrastructure."""
        return IAC_REPO.get(self.deployment)


class MappingSource(StrEnum):
    """What answered "which repository is this service?", in decreasing directness."""

    IMAGE = "image"
    SEED = "seed"
    PATTERN = "pattern"
    MANUAL = "manual"


class CommitSource(StrEnum):
    """What said which commit a workload is running, in decreasing directness.

    The image tag is the build itself; a GitHub tag of the same name is the same
    build, named once removed; the default branch is neither — it is what the
    repository looked like, which is a different claim and has to read as one.
    """

    IMAGE_TAG = "image_tag"
    GITHUB_TAG = "github_tag"
    DEFAULT_BRANCH = "default_branch"


class WorkloadEntry(BaseModel):
    """One running service, joined to the repository whose code it runs (M6).

    Keyed on the service name Datadog uses, which for the one mono-tenant
    application is a customer name that no repository claims. ``source`` is kept
    because a mapping derived from the image that was running and a mapping
    guessed from a name pattern are different facts, and a diagnosis built on the
    second must not read like one built on the first.
    """

    service: str
    repository: str = Field(description="Repository name, as the image and the seed name it.")
    repo_url: str | None = Field(
        default=None, description="From config.yaml; None when no team declares this repository."
    )
    image: str | None = Field(default=None, description="Image reference exactly as observed.")
    image_digest: str | None = None
    deployed_commit: MaybeUnknown
    commit_source: CommitSource | None = Field(
        default=None, description="What answered the commit; None when nothing did."
    )
    iac_repo: str | None = None
    iac_repo_url: str | None = None
    iac_paths: list[str] = Field(default_factory=list)
    tenancy: Tenancy | Unknown
    source: MappingSource


class MappingOutcome(StrEnum):
    """What one service's derivation produced. The report is a count per value."""

    MAPPED = "mapped"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    UNRESOLVED_IMAGE = "unresolved_image"
    NOT_MAPPED = "not_mapped"


class Derivation(BaseModel):
    """One service, after the derivation ran: what it produced and why.

    A service that produced no entry still produces a line — the reason it did
    not — because an unmapped production workload is Triage's own gap and a
    silent one is invisible.
    """

    service: str
    outcome: MappingOutcome
    reason: Filled
    entry: WorkloadEntry | None = None

    @property
    def mapped(self) -> bool:
        return self.outcome is MappingOutcome.MAPPED


class SystemMapKind(StrEnum):
    """The two kinds of row the map holds; with the name, it is the key of a row."""

    SERVICE = "service"
    TERRAFORM_MODULE = "terraform_module"


class ServiceEntry(BaseModel):
    """One service as the map records it: what it is, who owns it, where its code and infra are."""

    name: str
    repo_url: str
    team: str | None = Field(
        default=None, description="Owning team from config.yaml; None when undeclared."
    )
    source_commit: str | None = Field(
        default=None, description="Commit the summary was produced from (ADR-0006)."
    )
    summary: RepoSummary
    terraform_resources: list[TerraformResource] = Field(default_factory=list)


class TerraformModuleEntry(BaseModel):
    """One Terraform module as the map records it."""

    name: str
    repo_url: str
    team: str | None = None
    source_commit: str | None = None
    mapping: ModuleMapping
    resources: list[TerraformResource] = Field(default_factory=list)


class SystemMap(BaseModel):
    """The merged view: every service and Terraform module Triage knows about."""

    services: list[ServiceEntry] = Field(default_factory=list)
    terraform_modules: list[TerraformModuleEntry] = Field(default_factory=list)

    def service(self, name: str) -> ServiceEntry | None:
        return next((entry for entry in self.services if entry.name == name), None)
