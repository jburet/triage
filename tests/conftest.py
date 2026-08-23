"""Shared builders. Everything here is offline: no database, no network, no spend."""

import json
from pathlib import Path

import pytest

from triage.config import Config, load_config
from triage.db.repo import InMemoryRepository
from triage.integrations.base import FakeJiraClient, FakeSlackClient
from triage.llm import FakeLLM
from triage.runtime import DEPS_KEY, Deps
from triage.schemas import (
    DedupDecision,
    Diagnosis,
    ReviewVerdict,
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


def build_deps(
    config: Config,
    *,
    dedup: object = None,
    drafts: object = None,
    verdicts: object = None,
    repo: InMemoryRepository | None = None,
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
        config=config,
    )


def run_config(deps: Deps) -> dict[str, object]:
    return {"configurable": {DEPS_KEY: deps}}


def loaded_fixture_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())
