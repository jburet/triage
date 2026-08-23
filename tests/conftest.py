"""Shared builders. Everything here is offline: no database, no network, no spend."""

import json
from pathlib import Path

import pytest

from triage.analysis.runner import AnalysisRunner, FakeAnalysisRunner
from triage.config import Config, load_config
from triage.db.repo import InMemoryRepository, SystemMapEntry
from triage.integrations.base import FakeJiraClient, FakeSlackClient
from triage.integrations.github import FakeGitHubClient
from triage.llm import FakeLLM
from triage.runtime import DEPS_KEY, Deps
from triage.schemas import (
    AnalysisFindings,
    AnalysisKind,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStatus,
    CauseType,
    DedupDecision,
    Diagnosis,
    DiagnosisDraft,
    Hypothesis,
    RepoSummary,
    ReviewVerdict,
    ServiceEntry,
    SystemMapKind,
    TerraformModuleEntry,
    TerraformSummary,
    TicketDraft,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "diagnoses"
REPO_ROOT = Path(__file__).resolve().parents[1]


def load_diagnosis(name: str) -> Diagnosis:
    return Diagnosis.model_validate_json((FIXTURE_DIR / f"{name}.json").read_text())


def all_fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


@pytest.fixture
def config() -> Config:
    return load_config(REPO_ROOT / "config.yaml")


@pytest.fixture
def oom_diagnosis() -> Diagnosis:
    """High confidence, everything known. The path that should produce a ticket."""
    return load_diagnosis("oom_payments")


@pytest.fixture
def low_confidence_diagnosis() -> Diagnosis:
    """Low confidence, cause unknown. The path that should produce a Slack notice."""
    return load_diagnosis("latency_low_confidence")


def a_draft(**overrides: str) -> TicketDraft:
    """A draft that satisfies the schema, so tests can vary one field at a time."""
    base = {
        "summary": "payments-api OOM-killed 11 times during the settlement window",
        "symptom": "Pods were OOM-killed 11 times between 02:10 and 02:55 UTC.",
        "impact": "4.1% of POST /payments returned 502; 38% of the error budget consumed.",
        "probable_cause": "Unbounded idempotency-key cache from 9f2c1ab. Confidence: high.",
        "evidence": "Memory graph, 11 OOMKilled events, the deploy of 9f2c1ab.",
        "location": "github.com/org/payments-api at 9f2c1ab, src/payments/idempotency.py.",
        "expected_change": "Working set under 700 MB and no OOMKilled events over 24 h.",
        "out_of_scope": "Do not raise the container memory limit.",
        "ruled_out": "Connection-pool leak: pool size was flat. Traffic increase: rate unchanged.",
        "unknowns": "Peak per-entry size is not known; no heap dump was captured.",
    }
    base.update(overrides)
    return TicketDraft.model_validate(base)


def a_verdict(passes: bool = True, feedback: str = "") -> ReviewVerdict:
    default = "" if passes else "Fix the location."
    return ReviewVerdict(passes=passes, feedback=feedback or default)


def no_match(reason: str = "Different cause.") -> DedupDecision:
    return DedupDecision(matched=False, reasoning=reason)


def a_repo_summary(**overrides: object) -> RepoSummary:
    base: dict[str, object] = {
        "repo_url": "github.com/org/payments-api",
        "service": "payments-api",
        "languages": ["Python 3.12"],
        "frameworks": ["FastAPI", "SQLAlchemy"],
        "entry_points": [
            {"kind": "http_server", "name": "app", "path": "src/payments/main.py"},
            {"kind": "consumer", "name": "settlement worker", "path": "src/payments/worker.py"},
        ],
        "endpoints": [
            {
                "method": "POST",
                "path": "/payments",
                "handler": "create_payment in src/payments/api.py",
            }
        ],
        "depends_on": [
            {"target": "ledger-api", "kind": "http", "seen_at": "src/payments/ledger.py"}
        ],
        "database_access": [
            {
                "datastore": "payments-primary",
                "access": "SQLAlchemy ORM, Alembic migrations under db/migrations.",
                "tables": ["payments", "idempotency_keys"],
            }
        ],
        "observability": {
            "metrics": "Datadog StatsD, emitted from src/payments/metrics.py.",
            "logging": "structlog JSON to stdout, collected by the Datadog agent.",
            "tracing": "ddtrace auto-instrumentation on the HTTP server.",
            "dashboards": ["payments-overview"],
        },
    }
    base.update(overrides)
    return RepoSummary.model_validate(base)


def a_terraform_summary(**overrides: object) -> TerraformSummary:
    base: dict[str, object] = {
        "repo_url": "github.com/org/infra",
        "resources": [
            {
                "address": "module.payments.aws_db_instance.primary",
                "type": "aws_db_instance",
                "sizing": "db.r6g.large, 200 GB gp3.",
                "serves": "payments-api",
            }
        ],
        "networking": [
            {
                "subject": "payments private subnet",
                "detail": "10.0.16.0/20 in eu-west-1a, no route to an internet gateway.",
            }
        ],
        "managed_databases": [
            {
                "name": "payments-primary",
                "engine": "PostgreSQL 16",
                "address": "module.payments.aws_db_instance.primary",
                "sizing": "db.r6g.large, single-AZ.",
            }
        ],
        "modules": [
            {
                "module": "modules/payments",
                "services": ["payments-api"],
                "purpose": "Database, queues and IAM for the payments service.",
            }
        ],
    }
    base.update(overrides)
    return TerraformSummary.model_validate(base)


def some_findings(**overrides: object) -> AnalysisFindings:
    base: dict[str, object] = {
        "answer": "The idempotency-key cache is unbounded and grows with request volume.",
        "findings": [
            {
                "statement": "`_CACHE` is a module-level dict with no eviction.",
                "why_it_matters": "Working set grows until the container memory limit is hit.",
                "paths": ["src/payments/idempotency.py"],
            }
        ],
        "confidence": "high",
    }
    base.update(overrides)
    return AnalysisFindings.model_validate(base)


CANNED_PAYLOADS = {
    AnalysisKind.SUMMARIZE_REPO: a_repo_summary,
    AnalysisKind.SUMMARIZE_TERRAFORM: a_terraform_summary,
    AnalysisKind.CODE_ANALYSIS: some_findings,
    AnalysisKind.IAC_ANALYSIS: some_findings,
    AnalysisKind.DIFF_ANALYSIS: some_findings,
}


def an_analysis_request(
    kind: AnalysisKind = AnalysisKind.SUMMARIZE_REPO, **overrides: object
) -> AnalysisRequest:
    base: dict[str, object] = {
        "kind": kind,
        "repo_url": "github.com/org/payments-api",
        "commit": "9f2c1ab",
        "question": "Summarise this repository for the system map.",
    }
    if kind is AnalysisKind.DIFF_ANALYSIS:
        base["base_commit"] = "1111111"
    base.update(overrides)
    return AnalysisRequest.model_validate(base)


def an_analysis_result(kind: AnalysisKind = AnalysisKind.SUMMARIZE_REPO) -> AnalysisResult:
    return AnalysisResult(
        kind=kind, status=AnalysisStatus.SUCCEEDED, result=CANNED_PAYLOADS[kind]()
    )


def canned_runner() -> FakeAnalysisRunner:
    """A fake that answers every kind, for tests that only care that an analysis happened."""
    return FakeAnalysisRunner(results={kind: an_analysis_result(kind) for kind in AnalysisKind})


def a_service_entry(name: str = "payments-api", **overrides: object) -> ServiceEntry:
    base: dict[str, object] = {
        "name": name,
        "repo_url": "github.com/org/payments-api",
        "team": "payments",
        "source_commit": "9f2c1ab",
        "summary": a_repo_summary(service=name),
    }
    base.update(overrides)
    return ServiceEntry.model_validate(base)


def a_module_entry(name: str = "modules/payments", **overrides: object) -> TerraformModuleEntry:
    terraform = a_terraform_summary()
    base: dict[str, object] = {
        "name": name,
        "repo_url": "github.com/org/infra",
        "team": "platform",
        "source_commit": "abc1234",
        "mapping": terraform.modules[0],
        "resources": terraform.resources,
    }
    base.update(overrides)
    return TerraformModuleEntry.model_validate(base)


def map_row(entry: ServiceEntry | TerraformModuleEntry, kind: SystemMapKind) -> SystemMapEntry:
    return SystemMapEntry(
        kind=kind,
        name=entry.name,
        team=entry.team,
        source_commit=entry.source_commit,
        payload=entry.model_dump(mode="json"),
    )


def mapped(*entries: ServiceEntry | TerraformModuleEntry) -> InMemoryRepository:
    """A repository whose system map already knows these services and modules."""
    repo = InMemoryRepository()
    for entry in entries:
        kind = (
            SystemMapKind.SERVICE
            if isinstance(entry, ServiceEntry)
            else SystemMapKind.TERRAFORM_MODULE
        )
        repo.system_map[(kind, entry.name)] = map_row(entry, kind)
    return repo


def a_hypothesis(
    cause_type: CauseType = CauseType.APP, rank_score: float = 0.9, **overrides: object
) -> Hypothesis:
    base: dict[str, object] = {
        "cause_type": cause_type,
        "service": "payments-api",
        "commit": None if cause_type is CauseType.DEPENDENCY else "9f2c1ab",
        "description": f"A {cause_type.value} cause: the idempotency cache grows unbounded.",
        "rank_score": rank_score,
    }
    base.update(overrides)
    return Hypothesis.model_validate(base)


def a_synthesis(**overrides: object) -> DiagnosisDraft:
    """A synthesis that satisfies Diagnosis, so tests can break one rule at a time."""
    base: dict[str, object] = {
        "chosen_hypothesis": 0,
        "symptom": {
            "description": "Pods were OOM-killed 11 times between 02:10 and 02:55 UTC.",
            "window": {"start": "2026-08-22T02:10:00Z", "end": "2026-08-22T02:55:00Z"},
        },
        "impact": {
            "users": "4.1% of POST /payments callers received a 502.",
            "services": ["payments-api"],
            "slos": "38% of the monthly error budget was consumed.",
        },
        "probable_cause": "An unbounded idempotency-key cache grows until the memory limit.",
        "confidence": "medium",
        "confidence_rationale": "The metric and the code agree, but no heap dump confirms it.",
        "evidence": [
            {
                "kind": "metric",
                "description": "container.memory.usage rose from 320 MB to the 1 GB limit.",
                "url": "https://app.datadoghq.eu/metric/1",
            }
        ],
        "paths": ["src/payments/idempotency.py"],
        "expected_change": {
            "statement": "Working set stays under 700 MB for 24 h.",
            "how_to_verify": "The payments-overview dashboard, memory panel.",
        },
        "out_of_scope": ["Do not raise the container memory limit."],
        "ruled_out": [],
        "unknowns": [],
    }
    base.update(overrides)
    return DiagnosisDraft.model_validate(base)


def build_deps(
    config: Config,
    *,
    dedup: object = None,
    drafts: object = None,
    verdicts: object = None,
    syntheses: object = None,
    repo: InMemoryRepository | None = None,
    runner: AnalysisRunner | None = None,
    changed: dict[str, list[str]] | None = None,
) -> Deps:
    """Assemble fakes. Anything not supplied gets a sensible passing default."""
    responses: dict[type, object] = {
        DedupDecision: dedup if dedup is not None else [no_match()],
        TicketDraft: drafts if drafts is not None else [a_draft()],
        ReviewVerdict: verdicts if verdicts is not None else [a_verdict()],
        DiagnosisDraft: syntheses if syntheses is not None else [a_synthesis()],
    }
    return Deps(
        llm=FakeLLM(responses=responses),  # type: ignore[arg-type]
        jira=FakeJiraClient(),
        slack=FakeSlackClient(),
        repo=repo or InMemoryRepository(),
        runner=runner or canned_runner(),
        github=FakeGitHubClient(changed=changed or {}),
        config=config,
    )


def run_config(deps: Deps) -> dict[str, object]:
    return {"configurable": {DEPS_KEY: deps}}


def loaded_fixture_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())
