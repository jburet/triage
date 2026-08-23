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
    service: Filled = Field(description="Service name this repository deploys as.")
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
