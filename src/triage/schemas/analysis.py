"""The graph → analysis contract, as data (architecture §7, ADR-0009).

A node never runs code analysis itself: it submits an :class:`AnalysisRequest`
and receives an :class:`AnalysisResult`. Everything in between — the shallow
clone, the sandbox, the Agent SDK — belongs to the runner, which is what lets one
node run against a fake in tests, a subprocess in development and a gVisor Job in
production.

The per-kind payload registry is the single place a result is admitted. A payload
that does not validate against the schema for its kind is a *failed* result
naming the kind, never a partial success: a half-parsed summary would be
persisted into the system map and then quietly believed by every later feature.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError, model_validator

from triage.schemas.common import Confidence, Filled, MaybeUnknown, Unknown
from triage.schemas.system_map import Listed, RepoSummary, TerraformSummary


class AnalysisKind(StrEnum):
    """What is being asked of the analysis. Decides the payload schema and the prompt."""

    SUMMARIZE_REPO = "summarize_repo"
    SUMMARIZE_TERRAFORM = "summarize_terraform"
    CODE_ANALYSIS = "code_analysis"
    IAC_ANALYSIS = "iac_analysis"
    DIFF_ANALYSIS = "diff_analysis"


class AnalysisStatus(StrEnum):
    """``running`` is a state of the result *row*; only the other two are results."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self is not AnalysisStatus.RUNNING


class Finding(BaseModel):
    """One thing an analysis found, and where."""

    statement: Filled
    why_it_matters: Filled = Field(description="What it implies for the question that was asked.")
    paths: list[str] = Field(
        default_factory=list, description="Files or symbols, relative to the repo root."
    )


class AnalysisFindings(BaseModel):
    """Answer to a question asked about code or IaC at a commit.

    Shared by the three investigative kinds. The Analysis sub-graph turns it into
    ``Evidence`` and ``RuledOut`` entries, so a finding that cannot be pointed at
    is of no use — hence the paths on each one.
    """

    answer: MaybeUnknown = Field(description="Direct answer to the question that was asked.")
    findings: Listed[Finding] | Unknown
    confidence: Confidence


Payload: TypeAlias = RepoSummary | TerraformSummary | AnalysisFindings

_PAYLOAD_SCHEMAS: Mapping[AnalysisKind, type[Payload]] = {
    AnalysisKind.SUMMARIZE_REPO: RepoSummary,
    AnalysisKind.SUMMARIZE_TERRAFORM: TerraformSummary,
    AnalysisKind.CODE_ANALYSIS: AnalysisFindings,
    AnalysisKind.IAC_ANALYSIS: AnalysisFindings,
    AnalysisKind.DIFF_ANALYSIS: AnalysisFindings,
}


def payload_schema(kind: AnalysisKind) -> type[Payload]:
    """The one schema a result of this kind may carry."""
    return _PAYLOAD_SCHEMAS[kind]


class AnalysisPayloadError(ValueError):
    """A result payload did not match the schema for its kind."""

    def __init__(self, kind: AnalysisKind, detail: str) -> None:
        super().__init__(
            f"{kind.value} result is not a valid {payload_schema(kind).__name__}: {detail}"
        )
        self.kind = kind


def _first_problems(error: ValidationError, limit: int = 3) -> str:
    problems = [
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
        for item in error.errors()[:limit]
    ]
    remaining = error.error_count() - len(problems)
    return "; ".join(problems) + (f" (+{remaining} more)" if remaining > 0 else "")


def parse_payload(kind: AnalysisKind, raw: object) -> Payload:
    """Validate a raw payload against the schema registered for ``kind``."""
    try:
        return payload_schema(kind).model_validate(raw)
    except ValidationError as exc:
        raise AnalysisPayloadError(kind, _first_problems(exc)) from exc


class AnalysisRequest(BaseModel):
    """One question, about one repository, at one commit."""

    request_id: UUID = Field(default_factory=uuid4)
    kind: AnalysisKind
    repo_url: str
    commit: str
    base_commit: str | None = Field(
        default=None, description="Only for diff_analysis: the commit to diff against."
    )
    paths: list[str] = Field(
        default_factory=list,
        description=(
            "Files the mapping says define this workload, read before the selection "
            "profile's own globs. Empty means nothing outside the profile is known."
        ),
    )
    question: Filled

    @model_validator(mode="after")
    def _commits_match_the_kind(self) -> AnalysisRequest:
        if self.kind is AnalysisKind.DIFF_ANALYSIS and not self.base_commit:
            raise ValueError("diff_analysis needs a base_commit to diff against")
        if self.kind is not AnalysisKind.DIFF_ANALYSIS and self.base_commit:
            raise ValueError(
                f"{self.kind.value} reads one tree; base_commit is only for diff_analysis"
            )
        return self

    @property
    def commits(self) -> tuple[str, ...]:
        """Every commit the clone must fetch, the one to check out first."""
        return (self.commit, self.base_commit) if self.base_commit else (self.commit,)


class AnalysisResult(BaseModel):
    """A terminal outcome. Either a payload of the kind's own schema, or a stated failure."""

    kind: AnalysisKind
    status: AnalysisStatus
    result: Payload | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> AnalysisResult:
        if not self.status.is_terminal:
            raise ValueError("'running' is a state of the result row, not a result")
        if self.status is AnalysisStatus.SUCCEEDED:
            if self.result is None:
                raise ValueError("a succeeded analysis must carry its payload")
            expected = payload_schema(self.kind)
            if not isinstance(self.result, expected):
                raise ValueError(
                    f"a {self.kind.value} result must be a {expected.__name__}, "
                    f"got {type(self.result).__name__}"
                )
        else:
            if self.result is not None:
                raise ValueError("an analysis that did not succeed must not carry a payload")
            if not self.error:
                raise ValueError("a failed analysis must say why")
        return self

    @property
    def succeeded(self) -> bool:
        return self.status is AnalysisStatus.SUCCEEDED

    @classmethod
    def failed(cls, kind: AnalysisKind, error: str) -> AnalysisResult:
        return cls(kind=kind, status=AnalysisStatus.FAILED, error=error)

    @classmethod
    def from_payload(cls, kind: AnalysisKind, raw: object) -> AnalysisResult:
        """Admit a raw payload, or refuse it as a failure naming the kind."""
        try:
            payload = parse_payload(kind, raw)
        except AnalysisPayloadError as exc:
            return cls.failed(kind, str(exc))
        return cls(kind=kind, status=AnalysisStatus.SUCCEEDED, result=payload)
