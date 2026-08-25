"""Dependency wiring for the graphs.

LangGraph nodes are plain functions, so their collaborators arrive through the
run configuration rather than through module-level globals. :class:`Deps` is
that bundle; :func:`build_deps` chooses real or fake implementations from
settings, and tests construct one directly.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from langchain_core.runnables import RunnableConfig

from triage.analysis.runner import (
    AnalysisRunner,
    FakeAnalysisRunner,
    KubernetesJobRunner,
    dry_run_result,
)
from triage.config import Config, Settings, get_config, get_settings
from triage.db.repo import InMemoryRepository, SqlRepository, TriageRepository
from triage.integrations.base import (
    FakeJiraClient,
    FakeSlackClient,
    JiraClient,
    SlackClient,
)
from triage.integrations.datadog import DatadogClient, FakeDatadogClient
from triage.integrations.github import GitHubClient, dry_run_github, unconfigured_github
from triage.integrations.platform import PlatformClient
from triage.llm import StructuredLLM, build_llm

log = structlog.get_logger(__name__)

DEPS_KEY = "deps"


@dataclass(frozen=True)
class Deps:
    llm: StructuredLLM
    jira: JiraClient
    slack: SlackClient
    repo: TriageRepository
    runner: AnalysisRunner
    github: GitHubClient
    datadog: DatadogClient
    platform: PlatformClient | None
    config: Config


def build_github(settings: Settings) -> GitHubClient:
    """The real client, or one that states the variable is unset.

    Not a startup refusal: ``build_deps`` is shared by every graph and only
    cartography and the service mapping read GitHub, so an empty token must not
    stop a run that never asks it anything.

    Public because a dry run still wants it. Dry run swaps every *write* for a
    recording fake, and GitHub is a read — the same reason the one-shot scripts
    already reach for a real Datadog client. Faking it made the mapping report
    "no commit resolved" for every workload on a path that could never have
    resolved one.
    """
    from triage.integrations.github import GitHubRestClient

    if not settings.github_token:
        log.warning(
            "github_token_unset",
            detail="no TRIAGE_GITHUB_TOKEN; cartography cannot tell what a merge changed "
            "and the service mapping cannot resolve a tag to a commit",
        )
        return unconfigured_github()
    return GitHubRestClient(settings.github_token)


def _build_runner(settings: Settings, config: Config, repo: TriageRepository) -> AnalysisRunner:
    if settings.dry_run:
        return FakeAnalysisRunner(default=dry_run_result)

    from triage.analysis.jobs import KubernetesJobApi

    job = config.analysis.job
    return KubernetesJobRunner(KubernetesJobApi(job.namespace), repo, job)


def build_deps(settings: Settings | None = None, config: Config | None = None) -> Deps:
    """Assemble real or fake collaborators according to ``TRIAGE_DRY_RUN``."""
    settings = settings or get_settings()
    config = config or get_config()

    if settings.dry_run:
        log.warning(
            "dry_run_enabled",
            detail="Jira and Slack writes are recorded, not sent; state is in-memory",
        )
        return Deps(
            llm=build_llm(settings),
            jira=FakeJiraClient(),
            slack=FakeSlackClient(),
            repo=InMemoryRepository(),
            runner=FakeAnalysisRunner(default=dry_run_result),
            github=dry_run_github(),
            datadog=FakeDatadogClient(),
            platform=None,
            config=config,
        )

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from triage.integrations.datadog import DatadogRestClient
    from triage.integrations.jira import JiraRestClient
    from triage.integrations.platform import PlatformRestClient
    from triage.integrations.slack import SlackWebClient

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    repo = SqlRepository(async_sessionmaker(engine, expire_on_commit=False))
    return Deps(
        llm=build_llm(settings),
        jira=JiraRestClient(
            settings.jira_base_url, settings.jira_user_email, settings.jira_api_token
        ),
        slack=SlackWebClient(settings.slack_bot_token),
        repo=repo,
        runner=_build_runner(settings, config, repo),
        github=build_github(settings),
        datadog=DatadogRestClient(
            settings.datadog_site, settings.datadog_api_key, settings.datadog_app_key
        ),
        platform=(
            PlatformRestClient(settings.platform_url, settings.platform_api_key)
            if settings.platform_url
            else None
        ),
        config=config,
    )


def deps_from_runnable_config(config: RunnableConfig | None) -> Deps:
    """Pull :class:`Deps` out of a LangGraph ``RunnableConfig``.

    Falls back to :func:`build_deps` so the graph is directly invocable from
    LangGraph Studio, where nothing injects dependencies.
    """
    configurable = (config or {}).get("configurable") or {}
    deps = configurable.get(DEPS_KEY)
    if isinstance(deps, Deps):
        return deps
    return build_deps()
