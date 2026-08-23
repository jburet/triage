"""Shared builders. Everything here is offline: no database, no network, no spend."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from triage.analysis.runner import AnalysisRunner, FakeAnalysisRunner
from triage.config import Config, Repo, RepoKind, load_config
from triage.db.repo import InMemoryRepository, SystemMapEntry
from triage.integrations.base import FakeJiraClient, FakeSlackClient
from triage.integrations.datadog import FakeDatadogClient
from triage.integrations.github import FakeGitHubClient
from triage.integrations.platform import FakePlatformClient
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
    WorkloadEntry,
)
from triage.schemas.alert import Alert
from triage.schemas.collection import (
    AlertClass,
    AlertClassification,
    FollowUpPlan,
    Qualification,
)
from triage.schemas.postmortem import Postmortem

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "diagnoses"
DATADOG_DIR = Path(__file__).parent / "fixtures" / "datadog"
CAPTURE = "hcl_software_uat_20260822"
TENANT = "plt-hcl-software-uat"
CAPTURED_DIGEST = "sha256:2e15f697553acdbdd13ec687080f1b600d531b504b73603dede0bda606d1d87b"
REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "reference-aws-architecture-2026-04-20.md"

SEED_HEADER = "| Repository | Role | Tech Stack | Tenancy Model | Deployment Method |"
SEED_SEPARATOR = "| --- | --- | --- | --- | --- |"


def seed_document(*rows: str, header: str = SEED_HEADER, separator: str = SEED_SEPARATOR) -> str:
    """A document shaped like the architecture one, carrying only the rows a test needs."""
    return "\n".join(["### 1.1 Repository Map", "", header, separator, *rows, ""])


def load_diagnosis(name: str) -> Diagnosis:
    return Diagnosis.model_validate_json((FIXTURE_DIR / f"{name}.json").read_text())


def all_fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


@pytest.fixture
def config() -> Config:
    return load_config(REPO_ROOT / "config.yaml")


def declaring(
    *urls: str,
    team: str = "platform",
    kind: RepoKind = RepoKind.APPLICATION,
    serves: tuple[str, ...] = (),
) -> Config:
    """The shipped config with exactly these repositories declared."""
    base = load_config(REPO_ROOT / "config.yaml")
    repos = [Repo(url=url, team=team, kind=kind, serves=list(serves)) for url in urls]
    return base.model_copy(update={"repos": repos})


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


def captured(name: str, slug: str = CAPTURE) -> dict:
    """One response from the Datadog capture, verbatim as the API returned it."""
    return json.loads((DATADOG_DIR / slug / f"{name}.json").read_text())


def captured_alert(slug: str = CAPTURE) -> Alert:
    """The StatefulSet-replicas alert, parsed from the event the poller would have read."""
    events = captured("events_kube_namespace", slug)["data"]
    firing = next(
        event
        for event in events
        if Alert.is_monitor_alert(event)
        and event["attributes"]["attributes"].get("monitor-alert-event", {}).get("alert_type")
        == "error"
    )
    return Alert.from_event(firing)


def pod_down_alert(slug: str = CAPTURE) -> Alert:
    """The same incident seen through the event monitor whose query *is* re-runnable.

    Its query counts container deletion events, so re-running it returns the three
    kills and their exit codes — which is the case behaviour 2.3 is about.
    """
    monitor = captured("monitor", slug)
    return captured_alert(slug).model_copy(
        update={
            "monitor_id": monitor["id"],
            "monitor_name": monitor["name"],
            "monitor_query": monitor["query"],
            "monitor_options": monitor["options"],
        }
    )


def fake_datadog(slug: str = CAPTURE, **overrides: object) -> FakeDatadogClient:
    """A client that replays the capture, keyed by what the query is about.

    Spans are deliberately absent from both the window and the widened check: the
    captured tenant has no APM at all, which is the case that separates "not
    instrumented" from "nothing happened" (ADR-0016).
    """
    responses: dict[str, dict[str, object]] = {
        "events": {
            "container_name:platform": captured("monitor_query_events", slug),
            "service:plt-hcl-software-uat": captured("events_service", slug),
            "kube_namespace:hcl-software-uat": captured("events_kube_namespace", slug),
        },
        "logs": {"plt-hcl-software-uat": captured("logs_at_alert", slug)},
        "logs_aggregate": {"plt-hcl-software-uat": captured("logs_aggregate", slug)},
        "metrics": {
            "kubernetes.containers.restarts": captured(
                "metric_kubernetes_containers_restarts", slug
            ),
            "kubernetes.memory.usage_pct": captured("metric_kubernetes_memory_usage_pct", slug),
            "statefulset.replicas_ready": captured(
                "metric_kubernetes_state_statefulset_replicas_ready", slug
            ),
            "statefulset.replicas_desired": captured(
                "metric_kubernetes_state_statefulset_replicas_desired", slug
            ),
        },
        "monitor": {str(captured("monitor", slug)["id"]): captured("monitor", slug)},
    }
    responses.update(overrides)  # type: ignore[arg-type]
    return FakeDatadogClient(responses=responses)


def a_workload(service: str = TENANT, **overrides: object) -> WorkloadEntry:
    """A workload as the derivation records it: the captured tenant, on its own image."""
    base: dict[str, object] = {
        "service": service,
        "repository": "platform",
        "repo_url": "github.com/zeenea/platform",
        "image": f"097607883991.dkr.ecr.us-east-1.amazonaws.com/platform:501@{CAPTURED_DIGEST}",
        "image_digest": CAPTURED_DIGEST,
        "deployed_commit": {
            "unknown": True,
            "reason": "the image was found, but its tag '501' is not a commit",
        },
        "iac_repo": "platform-infra",
        "tenancy": "mono_tenant",
        "source": "image",
    }
    base.update(overrides)
    return WorkloadEntry.model_validate(base)


def statefulset_change_event(slug: str = CAPTURE) -> dict:
    """The captured change-tracking event, the one carrying the workload's own image."""
    return next(
        event
        for event in captured("events_service", slug)["data"]
        if (event["attributes"]["attributes"].get("changed_resource") or {}).get("type")
        == "kube_stateful_set"
    )


def running_image(reference: str, slug: str = CAPTURE) -> dict:
    """That event, with the workload running some other image."""
    event = deepcopy(statefulset_change_event(slug))
    event["attributes"]["attributes"]["new_value"]["containers"] = [
        {"image": reference, "name": "workload"}
    ]
    return event


def datadog_running(reference: str, service: str = TENANT) -> FakeDatadogClient:
    """A client whose only answer is this service running this image."""
    replay = {"events": {f"service:{service}": {"data": [running_image(reference)]}}}
    return FakeDatadogClient(responses=replay, wide=replay)


def fake_datadog_over_days(slug: str = CAPTURE) -> FakeDatadogClient:
    """The same capture, answered for a query spanning days rather than an incident.

    A mapping pass asks about a week, which the fake would otherwise treat as the
    widened emptiness check and answer empty (ADR-0016).
    """
    replay = fake_datadog(slug)
    return FakeDatadogClient(responses=replay.responses, wide=replay.responses)


def a_classification(alert_class: AlertClass = AlertClass.CRASH_RESTART) -> AlertClassification:
    return AlertClassification(
        alert_class=alert_class,
        reason="The monitor counts container deletion events over five minutes.",
    )


def a_qualification(*causes: dict[str, object], **overrides: object) -> Qualification:
    base: dict[str, object] = {
        "summary": "The pod was killed three times with exit code 137 after failing "
        "its liveness probe during startup.",
        "causes": list(causes)
        or [
            {
                "cause_type": "infra",
                "service": "plt-hcl-software-uat",
                "description": "The liveness probe is shorter than the pod's own startup.",
                "rank_score": 0.9,
            }
        ],
    }
    base.update(overrides)
    return Qualification.model_validate(base)


def a_follow_up(*requests: dict[str, object], done: bool = False) -> FollowUpPlan:
    return FollowUpPlan.model_validate({"done": done, "requests": list(requests)})


def a_postmortem(**overrides: object) -> Postmortem:
    base: dict[str, object] = {
        "timeline": "00:43 probe failures; 00:43:54 container killed with exit code 137.",
        "what_happened": "The tenant's platform pod restarted three times in four minutes.",
        "why_it_happened": "The liveness probe is shorter than the startup. Confidence: medium.",
        "what_would_have_helped": "No APM on this tenant, so no request-level view.",
    }
    base.update(overrides)
    return Postmortem.model_validate(base)


def build_deps(
    config: Config,
    *,
    dedup: object = None,
    drafts: object = None,
    verdicts: object = None,
    syntheses: object = None,
    postmortems: object = None,
    classifications: object = None,
    qualifications: object = None,
    follow_ups: object = None,
    repo: InMemoryRepository | None = None,
    runner: AnalysisRunner | None = None,
    changed: dict[str, list[str]] | None = None,
    datadog: FakeDatadogClient | None = None,
    platform: FakePlatformClient | None = None,
) -> Deps:
    """Assemble fakes. Anything not supplied gets a sensible passing default."""
    responses: dict[type, object] = {
        DedupDecision: dedup if dedup is not None else [no_match()],
        TicketDraft: drafts if drafts is not None else [a_draft()],
        ReviewVerdict: verdicts if verdicts is not None else [a_verdict()],
        DiagnosisDraft: syntheses if syntheses is not None else [a_synthesis()],
        AlertClassification: classifications
        if classifications is not None
        else [a_classification()],
        Qualification: qualifications if qualifications is not None else [a_qualification()],
        FollowUpPlan: follow_ups if follow_ups is not None else [a_follow_up(done=True)],
        Postmortem: postmortems if postmortems is not None else [a_postmortem()],
    }
    return Deps(
        llm=FakeLLM(responses=responses),  # type: ignore[arg-type]
        jira=FakeJiraClient(),
        slack=FakeSlackClient(),
        repo=repo or InMemoryRepository(),
        runner=runner or canned_runner(),
        github=FakeGitHubClient(changed=changed or {}),
        datadog=datadog or FakeDatadogClient(),
        platform=platform,
        config=config,
    )


def run_config(deps: Deps) -> dict[str, object]:
    return {"configurable": {DEPS_KEY: deps}}


def loaded_fixture_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())
