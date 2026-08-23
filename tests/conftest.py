"""Shared builders. Everything here is offline: no database, no network, no spend."""

import json
from pathlib import Path

import pytest

from triage.analysis.runner import AnalysisRunner, FakeAnalysisRunner
from triage.config import Config, load_config
from triage.db.repo import InMemoryRepository
from triage.integrations.base import FakeJiraClient, FakeSlackClient
from triage.llm import FakeLLM
from triage.runtime import DEPS_KEY, Deps
from triage.schemas import (
    AnalysisFindings,
    AnalysisKind,
    AnalysisRequest,
    AnalysisResult,
    AnalysisStatus,
    DedupDecision,
    Diagnosis,
    RepoSummary,
    ReviewVerdict,
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


def build_deps(
    config: Config,
    *,
    dedup: object = None,
    drafts: object = None,
    verdicts: object = None,
    repo: InMemoryRepository | None = None,
    runner: AnalysisRunner | None = None,
) -> Deps:
    """Assemble fakes. Anything not supplied gets a sensible passing default."""
    responses: dict[type, object] = {
        DedupDecision: dedup if dedup is not None else [no_match()],
        TicketDraft: drafts if drafts is not None else [a_draft()],
        ReviewVerdict: verdicts if verdicts is not None else [a_verdict()],
    }
    return Deps(
        llm=FakeLLM(responses=responses),  # type: ignore[arg-type]
        jira=FakeJiraClient(),
        slack=FakeSlackClient(),
        repo=repo or InMemoryRepository(),
        runner=runner or canned_runner(),
        config=config,
    )


def run_config(deps: Deps) -> dict[str, object]:
    return {"configurable": {DEPS_KEY: deps}}


def loaded_fixture_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())
