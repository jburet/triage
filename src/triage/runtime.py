"""Dependency wiring for the graphs.

LangGraph nodes are plain functions, so their collaborators arrive through the
run configuration rather than through module-level globals. :class:`Deps` is
that bundle; :func:`build_deps` chooses real or fake implementations from
settings, and tests construct one directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

import structlog
from langchain_core.runnables import RunnableConfig

from triage.analysis.runner import (
    AnalysisRunner,
    FakeAnalysisRunner,
    KubernetesJobRunner,
    dry_run_result,
)
from triage.config import Config, LLMProvider, Settings, get_config, get_settings
from triage.db.repo import InMemoryRepository, SqlRepository, TriageRepository
from triage.integrations.base import (
    FakeJiraClient,
    FakeSlackClient,
    JiraClient,
    SlackClient,
)
from triage.integrations.datadog import DatadogClient, FakeDatadogClient
from triage.integrations.github import GitHubClient, dry_run_github
from triage.integrations.platform import PlatformClient
from triage.llm import AnthropicClient, LiteLLMClient, StructuredLLM, Tier

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


MODEL_SETTING = {
    "triage": "model_triage",
    "analysis": "model_analysis",
    "diagnosis": "model_diagnosis",
}


def _configured_models(settings: Settings) -> tuple[dict[Tier, str], list[str]]:
    """The tier-to-model mapping from `TRIAGE_MODEL_*`, and which are unset."""
    models: dict[Tier, str] = {}
    missing: list[str] = []
    for tier, attribute in MODEL_SETTING.items():
        value = getattr(settings, attribute)
        if value:
            models[cast(Tier, tier)] = value
        else:
            missing.append(f"TRIAGE_{attribute.upper()}")
    return models, missing


def build_llm(settings: Settings) -> StructuredLLM:
    """The proxy, or the API directly — the same one method either way (ADR-0007).

    ``auto`` is what makes the local shortcut usable without being a footgun: it
    takes the direct client only when there is an Anthropic key and the LiteLLM
    URL is still the default, so a deployment that configures a proxy keeps its
    guardrails even if a key happens to be in the environment.
    """
    provider = settings.llm_provider
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if provider is LLMProvider.AUTO:
        # Compared against the field default rather than a fresh Settings(), which
        # would re-read the environment and call a configured proxy "untouched".
        untouched_proxy = settings.litellm_url == type(settings).model_fields["litellm_url"].default
        provider = LLMProvider.ANTHROPIC if key and untouched_proxy else LLMProvider.LITELLM
    models, missing = _configured_models(settings)

    if provider is LLMProvider.LITELLM:
        # Unset, the tier is the model name — what a proxy configured for Triage
        # publishes. Set, they are what a proxy nobody will re-configure for us
        # calls those models. Half-set is neither, and would fail on one tier at
        # whatever hour that node first runs.
        if models and missing:
            raise ValueError(
                f"a proxy addressed by model name needs all three tiers; unset: "
                f"{', '.join(missing)}. Leave all three empty to address the proxy "
                f"by the aliases triage / analysis / diagnosis instead."
            )
        # Logged because "model not found" from a proxy is otherwise a guess about
        # which of the two addressings is in force.
        log.info(
            "llm_proxy",
            url=settings.litellm_url,
            addressed_by="model name" if models else "tier alias",
        )
        return LiteLLMClient(settings.litellm_url, settings.litellm_api_key, models=models)

    if missing:
        raise ValueError(
            f"calling Anthropic directly needs a model per tier; unset: {', '.join(missing)}. "
            f"See .env.example for the current ids."
        )
    if not key:
        log.warning(
            "anthropic_key_unset",
            detail="no TRIAGE_ANTHROPIC_API_KEY and no ANTHROPIC_API_KEY; the SDK will "
            "resolve its own credentials (environment, or an `ant auth login` profile)",
        )
    log.info("llm_direct", detail="calling Anthropic directly: the proxy's spend caps do not apply")
    return AnthropicClient(settings.anthropic_api_key, models)


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
    from triage.integrations.github import GitHubRestClient
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
        github=GitHubRestClient(settings.github_token),
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
