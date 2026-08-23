"""Running one analysis: the protocol, and the ways Triage satisfies it."""

from triage.analysis.runner import (
    AnalysisRunner,
    FakeAnalysisRunner,
    LocalAnalysisRunner,
    dry_run_result,
)

__all__ = ["AnalysisRunner", "FakeAnalysisRunner", "LocalAnalysisRunner", "dry_run_result"]
