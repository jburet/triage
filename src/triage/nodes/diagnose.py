"""Synthesising one Diagnosis from what the analyses found (architecture §2.1).

The model writes the reasoning; this node writes the facts. Where the fix goes,
which findings support it, what was not analysed and what failed all come from
the run itself, so a synthesis cannot relocate a bug into a repository nobody
looked at, and cannot quietly drop the hypothesis whose analysis crashed.

``Diagnosis`` validates its own confidence (`_confidence_is_earned`), which is
the one rule a model can plausibly break by writing a confident paragraph on top
of a single data point. A synthesis that breaks it is fed the validation error
and asked again, once; a second failure is degraded to ``low`` rather than
retried forever. Low confidence still reaches a human — through the pipeline's
Slack notice — and a run that produced nothing does not.
"""

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from triage.graphs.state import AnalysisState, Investigated
from triage.mapping.commits import CONFIDENCE_CAP, commit_caveat
from triage.prompts import render
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.analysis import AnalysisKind
from triage.schemas.common import Confidence, Feature, Unknown
from triage.schemas.diagnosis import (
    Diagnosis,
    DiagnosisDraft,
    Evidence,
    EvidenceKind,
    Location,
    OpenQuestion,
    RuledOut,
)

log = structlog.get_logger(__name__)

FINDING_EVIDENCE_KIND: dict[AnalysisKind, EvidenceKind] = {
    AnalysisKind.CODE_ANALYSIS: EvidenceKind.COMMIT,
    AnalysisKind.DIFF_ANALYSIS: EvidenceKind.COMMIT,
    AnalysisKind.IAC_ANALYSIS: EvidenceKind.OTHER,
}


def _analysis_payload(item: Investigated) -> dict[str, Any]:
    if item.result is None:
        return {"ran": False, "why": "dependency cause: no repository is analysed for it"}
    if not item.result.succeeded:
        return {"ran": True, "failed": item.result.error}
    findings = item.findings
    return {
        "ran": True,
        "findings": findings.model_dump(mode="json") if findings else None,
    }


def _analyses_section(investigated: list[Investigated]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "cause_type": item.hypothesis.cause_type.value,
            "hypothesis": item.hypothesis.description,
            "rank_score": item.hypothesis.rank_score,
            "repo": item.repo_url,
            "commit": item.commit,
            "base_commit": item.base_commit,
            "analysis": _analysis_payload(item),
        }
        for index, item in enumerate(investigated)
    ]


def _chosen(state: AnalysisState, draft: DiagnosisDraft) -> Investigated | None:
    investigated = state.get("investigated", [])
    index = draft.chosen_hypothesis
    if index is None or not 0 <= index < len(investigated):
        return None
    return investigated[index]


def _finding_evidence(chosen: Investigated | None) -> list[Evidence]:
    findings = chosen.findings if chosen else None
    if chosen is None or findings is None or isinstance(findings.findings, Unknown):
        return []
    assert chosen.result is not None
    kind = FINDING_EVIDENCE_KIND.get(chosen.result.kind, EvidenceKind.OTHER)
    return [
        Evidence(
            kind=kind,
            description=(
                f"{finding.statement} {finding.why_it_matters}"
                + (f" ({', '.join(finding.paths)})" if finding.paths else "")
            ),
        )
        for finding in findings.findings
    ]


def _location(chosen: Investigated | None, draft: DiagnosisDraft) -> Location:
    if chosen is None:
        absent = Unknown(
            reason="no analysed hypothesis was selected as the cause, so no repository "
            "or commit could be resolved"
        )
        return Location(
            repo=absent,
            commit=absent,
            paths=draft.paths,
            terraform_resource=draft.terraform_resource,
        )
    return Location(
        repo=chosen.repo_url
        or Unknown(reason=f"no repository is mapped to {chosen.hypothesis.service}"),
        commit=chosen.commit
        or Unknown(reason=f"no deployed commit is known for {chosen.hypothesis.service}"),
        paths=draft.paths,
        terraform_resource=draft.terraform_resource,
    )


def _failure_unknowns(investigated: list[Investigated]) -> list[OpenQuestion]:
    return [
        OpenQuestion(
            question=f"Was it caused by: {item.hypothesis.description}",
            why_unresolved=(
                f"The {item.result.kind.value} of this hypothesis did not run to completion: "
                f"{item.result.error}"
            ),
        )
        for item in investigated
        if item.failed and item.result is not None
    ]


def _confidence(draft: DiagnosisDraft, chosen: Investigated | None) -> Confidence:
    """Capped when the analysis the cause rests on never ran, or read the wrong tree.

    A cause the code was never read for can be the best available explanation; it
    cannot be a confirmed one (ADR-0004's rule). Neither can one read at a commit
    nothing established this service to be running (M6 2.16).
    """
    caps = [draft.confidence]
    if chosen is not None and chosen.failed:
        caps.append(Confidence.MEDIUM)
    if chosen is not None and chosen.commit_source in CONFIDENCE_CAP:
        caps.append(CONFIDENCE_CAP[chosen.commit_source])
    return min(caps, key=lambda level: level.rank)


def _rationale(draft: DiagnosisDraft, chosen: Investigated | None) -> str:
    """The model's reasoning, plus what the run knows about the tree it read."""
    caveat = commit_caveat(chosen.commit_source, chosen.repo_url) if chosen else None
    return f"{draft.confidence_rationale} {caveat}" if caveat else draft.confidence_rationale


def _assemble(state: AnalysisState, draft: DiagnosisDraft, *, degraded: bool = False) -> Diagnosis:
    investigated = state.get("investigated", [])
    chosen = _chosen(state, draft)
    evidence = [*draft.evidence, *_finding_evidence(chosen)]
    if degraded and not evidence:
        evidence = [
            Evidence(
                kind=EvidenceKind.OTHER,
                description="No checkable evidence was produced: every analysis this "
                "diagnosis relied on failed or returned nothing.",
            )
        ]
    return Diagnosis(
        signal_id=state.get("signal_id"),
        feature=state.get("feature", Feature.F1),
        service=state.get("service", ""),
        team=state.get("team", ""),
        symptom=draft.symptom,
        impact=draft.impact,
        probable_cause=draft.probable_cause,
        confidence=Confidence.LOW if degraded else _confidence(draft, chosen),
        confidence_rationale=_rationale(draft, chosen),
        evidence=evidence,
        location=_location(chosen, draft),
        expected_change=draft.expected_change,
        out_of_scope=draft.out_of_scope,
        ruled_out=[
            *draft.ruled_out,
            *(
                RuledOut(hypothesis=item.hypothesis.description, why=item.reason)
                for item in state.get("deferred", [])
            ),
        ],
        unknowns=[*draft.unknowns, *_failure_unknowns(investigated)],
    )


def _prompt(state: AnalysisState, correction: str | None = None) -> str:
    sections: dict[str, Any] = {
        "incident": {
            "service": state.get("service"),
            "team": state.get("team"),
            "feature": state.get("feature", Feature.F1).value,
        },
        "collected": state.get("context", {}),
        "analyses": _analyses_section(state.get("investigated", [])),
        "not_analysed": [
            {"hypothesis": item.hypothesis.description, "reason": item.reason}
            for item in state.get("deferred", [])
        ],
    }
    if correction is not None:
        sections["rejected_draft"] = correction
    return render("diagnose", **sections)


async def _synthesise(deps: Deps, prompt: str) -> DiagnosisDraft:
    return await deps.llm.call("diagnosis", prompt, DiagnosisDraft)


async def diagnose(state: AnalysisState, config: RunnableConfig | None = None) -> AnalysisState:
    deps = deps_from_runnable_config(config)
    draft = await _synthesise(deps, _prompt(state))
    try:
        return {"diagnosis": _assemble(state, draft), "synthesis_attempts": 1}
    except ValidationError as exc:
        rejection = str(exc)
        log.warning("diagnosis_rejected", error=rejection)

    retried = await _synthesise(
        deps,
        _prompt(
            state,
            correction=(
                "Your previous answer was rejected by the Diagnosis schema. Fix exactly "
                f"this and change nothing else:\n{rejection}"
            ),
        ),
    )
    try:
        return {"diagnosis": _assemble(state, retried), "synthesis_attempts": 2}
    except ValidationError as second:
        log.warning("diagnosis_degraded", error=str(second))
        return {
            "diagnosis": _assemble(state, retried, degraded=True),
            "synthesis_attempts": 2,
        }
