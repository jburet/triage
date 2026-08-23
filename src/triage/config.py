"""Configuration: ``config.yaml`` for static facts, environment for secrets.

Discovered data (the system map) lives in PostgreSQL and is never read from
here — that split is what stops the two drifting.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from triage.schemas.common import Confidence, Feature

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


PLATFORM_TEAM = "platform"
"""The team that owns Triage itself, and hears about the system it cannot attribute."""


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


class Repo(BaseModel):
    url: str
    team: str
    kind: RepoKind


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


class AnalysisJobConfig(BaseModel):
    """Where one analysis runs (ADR-0009).

    The cluster objects these name — the runtime class, the Secret, the
    NetworkPolicy, the narrow database role — belong to the infra track. Triage
    only names them when it submits a Job.
    """

    namespace: str = "triage"
    image: str = ""
    runtime_class: str = "gvisor"
    secret_ref: str = "triage-analysis"


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
    thresholds: Thresholds
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)

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

    model_config = SettingsConfigDict(env_prefix="TRIAGE_", env_file=".env", extra="ignore")

    litellm_url: str = "http://localhost:4000/v1"
    litellm_api_key: str = "sk-local-dev"
    database_url: str = "postgresql+psycopg://triage:triage@localhost:5432/triage"

    # When true, every write-capable integration is swapped for a recording fake.
    dry_run: bool = True

    slack_bot_token: str = ""

    # Read-only, and only ever used to compare two commits (ADR-0015).
    github_token: str = ""

    # Jira Cloud: basic auth with an Atlassian account email and an API token
    # (ADR-0013), not a bearer token.
    jira_base_url: str = ""
    jira_user_email: str = ""
    jira_api_token: str = ""

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
