"""Ways to run one analysis (architecture §7, ADR-0009).

One method, three implementations, chosen by where the code is running: a fake in
tests, a subprocess on the host for development and evals, and a gVisor
Kubernetes Job in production. The node that submits the request cannot tell them
apart, which is the point — an analysis is expensive and sandboxed, and no graph
should have to know how.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from triage.schemas.analysis import AnalysisKind, AnalysisRequest, AnalysisResult


class AnalysisRunner(Protocol):
    async def run(self, request: AnalysisRequest) -> AnalysisResult: ...


def dry_run_result(request: AnalysisRequest) -> AnalysisResult:
    """Dry run submits no Job, and says so instead of inventing a summary."""
    return AnalysisResult.failed(
        request.kind, "dry run: no analysis Job was submitted, so nothing was analysed"
    )


Canned = "AnalysisResult | Sequence[AnalysisResult]"


@dataclass
class FakeAnalysisRunner:
    """Canned results keyed by kind, recording what was asked.

    A sequence is consumed one call at a time and its last element repeats, the
    same rule as :class:`~triage.llm.FakeLLM`. An unconfigured kind is an
    assertion rather than a failed result: a test that forgot to arrange one
    would otherwise pass down the failure branch and prove nothing. Production
    dry-run supplies ``default`` instead.
    """

    results: Mapping[AnalysisKind, AnalysisResult | Sequence[AnalysisResult]] = field(
        default_factory=dict
    )
    default: Callable[[AnalysisRequest], AnalysisResult] | None = None
    requests: list[AnalysisRequest] = field(default_factory=list)
    _cursor: dict[AnalysisKind, int] = field(default_factory=dict)

    async def run(self, request: AnalysisRequest) -> AnalysisResult:
        self.requests.append(request)
        canned = self.results.get(request.kind)
        if canned is None:
            if self.default is not None:
                return self.default(request)
            raise AssertionError(
                f"FakeAnalysisRunner has no result configured for {request.kind.value}"
            )
        if isinstance(canned, AnalysisResult):
            return canned
        index = min(self._cursor.get(request.kind, 0), len(canned) - 1)
        self._cursor[request.kind] = index + 1
        return canned[index]

    def requests_for(self, kind: AnalysisKind) -> list[AnalysisRequest]:
        return [request for request in self.requests if request.kind is kind]
