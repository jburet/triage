"""Configuration: ``config.yaml`` for static facts, environment for secrets.

Discovered data (the system map) lives in PostgreSQL and is never read from
here — that split is what stops the two drifting.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from triage.schemas.common import Confidence, Feature

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


class Team(BaseModel):
    name: str
    slack_channel: str
    jira_project: str


class Repo(BaseModel):
    url: str
    team: str
    kind: str


class Database(BaseModel):
    name: str
    team: str
    secret_ref: str


class Thresholds(BaseModel):
    """Tunables the SRE team owns. See ADR-0002, ADR-0003."""

    ticket_confidence: dict[Feature, Confidence]
    dedup_recurrence_alert: int = Field(default=3, ge=1)
    dedup_recurrence_interval: int = Field(default=5, ge=1)
    max_compose_attempts: int = Field(default=3, ge=1)


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
    thresholds: Thresholds
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)

    def team(self, name: str) -> Team:
        for team in self.teams:
            if team.name == name:
                return team
        raise KeyError(f"team {name!r} is not declared in config.yaml")

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
