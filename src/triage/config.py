"""Configuration: ``config.yaml`` for static facts, environment for secrets.

Discovered data (the system map) lives in PostgreSQL and is never read from
here — that split is what stops the two drifting.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from triage.schemas.common import Confidence, Feature
from triage.schemas.errors import ErrorPersona, ErrorTrack

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


PLATFORM_TEAM = "platform"
"""The team that owns Triage itself, and hears about the system it cannot attribute."""


class LLMProvider(StrEnum):
    """Which implementation of :class:`~triage.llm.StructuredLLM` serves a run."""

    AUTO = "auto"
    LITELLM = "litellm"
    ANTHROPIC = "anthropic"


class WriteTargets(StrEnum):
    """Which systems a deployment of Triage is allowed to write to (ADR-0023).

    The first release is ``slack``: Jira issue creation is gated off here rather
    than deleted, so the composer, the self-review and the client stay in the
    tree and stay tested and the decision is reversible by one line of YAML.
    """

    SLACK = "slack"
    SLACK_AND_JIRA = "slack_and_jira"


class RepoKind(StrEnum):
    """What kind of summary a repository earns. An undeclared kind fails config load."""

    APPLICATION = "application"
    TERRAFORM = "terraform"


class Team(BaseModel):
    """One owning team, and how an alert is matched to it (ADR-0017).

    Scope is matched by pattern and never by enumeration: one StatefulSet monitor
    fired for 66 distinct groups in 40 days and the groups are per-customer
    tenants, so a list of services here would be obsolete the day a customer is
    provisioned.
    """

    name: str
    slack_channel: str
    jira_project: str
    service_patterns: list[str] = Field(default_factory=list)
    namespace_patterns: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    alert_persistence_minutes: int | None = Field(
        default=None, ge=0, description="Overrides the global gate for this team (ADR-0018)."
    )


def _url_name(url: str) -> str:
    """The repository's own name, as the last segment of its remote URL."""
    return url.rstrip("/").rsplit("/", 1)[-1]


class Repo(BaseModel):
    url: str
    team: str
    kind: RepoKind
    image_name: str | None = Field(
        default=None,
        description=(
            "The name the container image and the seed use for this repository, when "
            "the GitHub remote is spelled differently — the image is `platform` where "
            "the remote is `zeenea/datacatalog`. Unset, the URL's last segment is the "
            "name, which is right for every repository whose remote is named after it."
        ),
    )
    serves: list[str] = Field(
        default_factory=list,
        description=(
            "Service-name patterns this repository is deployed as. A multi-tenant "
            "platform runs one instance per customer — plt-merck-qa, plt-hcl-software-uat "
            "— and none of them is its own repository, so the system map, which is "
            "keyed on the name a repository says it deploys as, will never contain them."
        ),
    )
    defines: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "For an IaC repository: which of its paths define each workload it "
            "provisions, keyed by the repository that workload runs. Declared because "
            "no rule can find it — `platform-infra` organises by what it provisions "
            "on, `terraform/eks_module/`, and the workload's name is a resource label "
            "inside the file rather than a directory (ADR-0021). A workload with no "
            "key here falls back to the naming rule."
        ),
    )
    tag_template: str | None = Field(
        default=None,
        description=(
            "How this repository spells, as a git tag, the tag its images carry — "
            "`v{tag}`, `build-{tag}`. Unset means the two are the same string. Only the "
            "declared spelling is ever looked up: a tag invented by guessing points "
            "somewhere specific and wrong."
        ),
    )

    @field_validator("tag_template")
    @classmethod
    def _places_the_image_tag(cls, value: str | None) -> str | None:
        if value is not None and "{tag}" not in value:
            raise ValueError(
                f"{value!r} places no {{tag}}, so every image of this repository would "
                f"resolve to the same git tag"
            )
        return value

    @property
    def name(self) -> str:
        """What an image, the seed and a workload all call this repository."""
        return self.image_name or _url_name(self.url)

    def declared_paths(self, repository: str) -> list[str]:
        """Where this IaC repository says the named workload is defined, if it says."""
        return self.defines.get(repository, [])

    def github_tag(self, image_tag: str | None) -> str | None:
        """The git tag this image tag is spelled as here, or None when it carries none."""
        if image_tag is None:
            return None
        return (self.tag_template or "{tag}").format(tag=image_tag)


class Database(BaseModel):
    name: str
    team: str
    secret_ref: str


class Thresholds(BaseModel):
    """Tunables the SRE team owns. See ADR-0002, ADR-0003, ADR-0018."""

    ticket_confidence: dict[Feature, Confidence]
    dedup_recurrence_alert: int = Field(default=3, ge=1)
    dedup_recurrence_interval: int = Field(default=5, ge=1)
    max_compose_attempts: int = Field(default=3, ge=1)

    # An alert is analysed only once it has persisted this long (ADR-0018): across
    # 961 measured pod-down cycles the longest was nine minutes, so a 15-minute
    # gate discards exactly what a human would not have ticketed either.
    alert_persistence_minutes: int = Field(default=15, ge=0)
    p1_alert_persistence_minutes: int = Field(default=5, ge=0)
    flap_count: int = Field(default=5, ge=2)
    flap_window_hours: int = Field(default=24, ge=1)


class CollectionConfig(BaseModel):
    """Caps on what one collection may spend (ADR-0016).

    Reduction is the design, not an optimisation: sixty log entries came back as
    176 KB from the reference incident — roughly 45k tokens for one collector,
    against a 500k per-run budget that also has to cover analysis and composition.
    """

    window_multiplier: int = Field(default=4, ge=1)
    window_min_minutes: int = Field(default=15, ge=1)
    window_max_hours: int = Field(default=6, ge=1)
    widen_days: int = Field(default=7, ge=1)
    max_followup_calls: int = Field(default=6, ge=0)
    max_log_templates: int = Field(default=15, ge=1)
    max_log_lines: int = Field(default=25, ge=1)
    max_events: int = Field(default=40, ge=1)
    max_timeseries_series: int = Field(default=6, ge=1)
    max_timeseries_points: int = Field(default=60, ge=2)
    max_prompt_bytes: int = Field(default=60_000, ge=1_000)


class ErrorsConfig(BaseModel):
    """The hourly code-exception pass, and what it refuses to look at (ADR-0025).

    ``min_occurrences`` is a floor per tick and ``cumulative_occurrences`` the
    escalation that keeps a slow bleed from being invisible forever. Both were
    corrected on 2026-08-25 against 24 consecutive hourly ticks run live — the
    first measurement of the population the gate actually sees, which is one
    count per *group* per *tick* over issues that were new or regressed. Eleven
    groups arrived in the day, at 1, 1, 1, 2, 3, 4, 5, 30, 189, 7758 and 37691
    occurrences; nothing lands between 6 and 29, so every floor in that range
    behaves the same and ten reports four of the eleven on arrival.

    Since the escalation is fed by the occurrences that go on happening
    (ADR-0030), the floor delays rather than drops: over that day every floor
    from 5 to 200 produced the same five reports. A floor of 1 produced seven,
    five of them in one wave — the error stream ADR-0023 says to watch for.
    """

    tracks: list[ErrorTrack] = Field(
        default_factory=lambda: [ErrorTrack.TRACE, ErrorTrack.LOGS],
        description=(
            "One search per track. The org's `logs` track answered empty at every "
            "window and persona tried on 2026-08-25 — its Error Tracking is fed by "
            "APM spans alone — and it is asked anyway, for eleven bytes an hour, "
            "because an empty answer is evidence and a track nobody asks about is not."
        ),
    )
    persona: ErrorPersona = ErrorPersona.BACKEND
    min_occurrences: int = Field(default=10, ge=1)
    cumulative_occurrences: int = Field(default=100, ge=1)
    max_groups_per_tick: int = Field(default=5, ge=1)
    lookback_minutes: int = Field(default=60, ge=1)
    reanalyse_after: int = Field(
        default=168, ge=1, description="Hours before an already-reported group is looked at again."
    )


class JobResources(BaseModel):
    """What one analysis may consume before the kubelet stops it.

    A ceiling is what turns "the Job never came back" into a failure with a
    reason: without a memory limit an analysis that reads too much is an evicted
    node, and without an ephemeral-storage limit a clone that is larger than
    expected fills the disk under every other pod on it.
    """

    requests: dict[str, str] = Field(
        default_factory=lambda: {"cpu": "250m", "memory": "512Mi", "ephemeral-storage": "1Gi"}
    )
    limits: dict[str, str] = Field(
        default_factory=lambda: {"cpu": "1", "memory": "2Gi", "ephemeral-storage": "8Gi"}
    )


class AnalysisJobConfig(BaseModel):
    """Where one analysis runs (ADR-0009).

    The cluster objects these name — the runtime class, the Secret, the
    NetworkPolicy, the narrow database role — belong to the infra track and live
    as manifests under ``deploy/``. Triage only names them when it submits a Job.
    """

    namespace: str = "triage"
    image: str = ""
    runtime_class: str = ""
    """Empty is no ``runtimeClassName`` at all — the node's own runtime (ADR-0024).

    gVisor was chosen for a Job that ran an agent with tool use. ADR-0014 removed
    the agent, and what is left never executes the code it reads. Set this the day
    it does."""
    secret_ref: str = "triage-analysis"
    service_account: str = "triage-analysis"
    resources: JobResources = Field(default_factory=JobResources)


class AnalysisConfig(BaseModel):
    """Secondary-cause fan-out in the Analysis sub-graph. See ADR-0005."""

    min_rank_score: float = Field(default=0.3, ge=0.0, le=1.0)
    max_hypotheses: int = Field(default=3, ge=1)
    job: AnalysisJobConfig = Field(default_factory=AnalysisJobConfig)


class Config(BaseModel):
    teams: list[Team] = Field(default_factory=list)
    repos: list[Repo] = Field(default_factory=list)
    databases: list[Database] = Field(default_factory=list)
    clusters: dict[str, str] = Field(
        default_factory=dict,
        description="Datadog kube_cluster_name → environment. No alert carries a usable "
        "env: tag, so this map is the only thing that separates production (ADR-0017).",
    )
    writes: WriteTargets = WriteTargets.SLACK
    thresholds: Thresholds
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    errors: ErrorsConfig = Field(default_factory=ErrorsConfig)

    @property
    def files_tickets(self) -> bool:
        return self.writes is WriteTargets.SLACK_AND_JIRA

    def repo_serving(self, service: str, kind: RepoKind) -> Repo | None:
        """The repository declared as deploying this service, when exactly one is.

        The system map is keyed on the name a repository says it deploys as, which
        no per-customer instance of a multi-tenant platform will ever match. Two
        candidates is an ambiguity: analysing either would read a tree that does
        not run this tenant, so nothing is returned and the caller says why.
        """
        matches = [
            repo
            for repo in self.repos
            if repo.kind is kind and any(fnmatch(service, pattern) for pattern in repo.serves)
        ]
        return matches[0] if len(matches) == 1 else None

    def repo_named(self, name: str) -> Repo | None:
        """The declared repository this name refers to.

        The seed and a container image both name a repository without a host, so
        this is the join between them and the URLs config.yaml declares. A
        declared ``image_name`` is that join stated outright, and replaces the URL
        rather than adding to it: a repository that says it is called `platform`
        is not also answering to `datacatalog`.
        """
        return next((repo for repo in self.repos if repo.name == name), None)

    def repo_by_url(self, url: str) -> Repo | None:
        """The declared repository at this remote. The join back from a resolved deployment."""
        return next((repo for repo in self.repos if repo.url == url), None)

    def environment_of(self, cluster: str | None) -> str | None:
        """The environment a cluster runs, or None — never a guess (ADR-0017)."""
        return self.clusters.get(cluster) if cluster else None

    def persistence_minutes(self, team: str | None, priority: int | None) -> int:
        """How long an alert must have persisted before it is analysed (ADR-0018)."""
        thresholds = self.thresholds
        if priority == 1:
            return thresholds.p1_alert_persistence_minutes
        if team is not None and self.declares_team(team):
            override = self.team(team).alert_persistence_minutes
            if override is not None:
                return override
        return thresholds.alert_persistence_minutes

    def team(self, name: str) -> Team:
        for team in self.teams:
            if team.name == name:
                return team
        raise KeyError(f"team {name!r} is not declared in config.yaml")

    def declares_team(self, name: str) -> bool:
        return any(team.name == name for team in self.teams)

    def platform_channel(self) -> str:
        """Where Triage reports on itself. A missing platform team is a config error."""
        try:
            return self.team(PLATFORM_TEAM).slack_channel
        except KeyError as exc:
            raise KeyError(
                f"config.yaml declares no {PLATFORM_TEAM!r} team, so Triage has nowhere "
                f"to report what it could not attribute"
            ) from exc

    def confidence_threshold(self, feature: Feature) -> Confidence:
        try:
            return self.thresholds.ticket_confidence[feature]
        except KeyError as exc:  # pragma: no cover - config error, not a runtime path
            raise KeyError(
                f"no ticket_confidence threshold configured for {feature.value}"
            ) from exc


class Settings(BaseSettings):
    """Secrets and endpoints. Never committed; see ``.env.example``."""

    # The .env belongs to the checkout, not to whatever directory the process was
    # started in: the analysis entrypoint runs with a throwaway clone as its working
    # directory, and a CWD-relative env file silently gave it stock defaults — a
    # proxy on localhost that is not there — rather than the configuration.
    model_config = SettingsConfigDict(
        env_prefix="TRIAGE_", env_file=(".env", DEFAULT_CONFIG_PATH.parent / ".env"), extra="ignore"
    )

    # How model calls are made. ``auto`` prefers the proxy and falls back to the
    # direct Anthropic client when an API key is set and no proxy is configured —
    # which is the local-development case, and the only reason this second path
    # exists (ADR-0007, amended).
    llm_provider: LLMProvider = LLMProvider.AUTO

    litellm_url: str = "http://localhost:4000/v1"
    litellm_api_key: str = "sk-local-dev"

    # Direct Anthropic access. The three model ids are configuration and not code,
    # so that "graph code asks for a tier, never a model" still holds: an unset one
    # is an error naming the variable, never a default compiled in here.
    anthropic_api_key: str = ""
    model_triage: str = ""
    model_analysis: str = ""
    model_diagnosis: str = ""
    database_url: str = "postgresql+psycopg://triage:triage@localhost:5432/triage"

    # When true, every write-capable integration is swapped for a recording fake.
    dry_run: bool = True

    slack_bot_token: str = ""

    # Read-only: comparing two commits (ADR-0015), resolving a tag or the default
    # branch to the commit it points at, and listing a repository's tree (M6).
    # Unset is not an error — it degrades cartography and the mapping, and says so.
    github_token: str = ""

    # Jira Cloud: basic auth with an Atlassian account email and an API token
    # (ADR-0013), not a bearer token.
    jira_base_url: str = ""
    jira_user_email: str = ""
    jira_api_token: str = ""

    # LangGraph Platform. Unset means the in-process fallback (ADR-0011): the same
    # graph, the same thread id, no queue.
    platform_url: str = ""
    platform_api_key: str = ""

    # Datadog: read-only collection for F1. ``datadog_site`` is the API host;
    # the application key is scoped and belongs to a service account, not a person.
    datadog_site: str = "api.datadoghq.eu"
    datadog_api_key: str = ""
    datadog_app_key: str = ""

    config_path: Path = DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> Config:
    """Read and validate ``config.yaml``. Not cached, so tests can point elsewhere."""
    resolved = path or get_settings().config_path
    raw: Any = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return Config.model_validate(raw)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@functools.lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
