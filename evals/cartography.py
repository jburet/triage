"""Scored F0 summaries against real public repositories.

Deliberately not part of CI: it clones over the network and calls the ``analysis``
tier, so it costs money and time on every run. Its result is a number to watch
rather than a pass/fail gate.

    make evals-cartography

The metric is **coverage**: the share of a summary's areas that came back filled
rather than as an ``Unknown``. Coverage alone is not quality — a model that
invents scores perfectly — so every run also prints each ``Unknown`` reason and
the whole summary. The reasons are the interesting output: they say whether a gap
is honest ("this repository declares no scheduled jobs") or a failure of the
selection ("the router is in a file that was not read"), and the second kind is
what fixes ``triage.analysis.context``.

Correctness of the *filled* areas is a human read. Nothing here can score it, and
a rubric that pretended to would be the same invention the schemas exist to stop.

The commit is resolved from the remote's current tip, so two runs on different
days are not comparable. Pin ``commit`` on a case once a score is worth tracking.
"""

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field

from triage.analysis.runner import LocalAnalysisRunner
from triage.analysis.summaries import repo_summary_request, terraform_summary_request
from triage.schemas.analysis import AnalysisKind, AnalysisRequest, AnalysisResult
from triage.schemas.common import Unknown

ENTRYPOINT = [sys.executable, "-m", "triage.analysis.entrypoint"]

AREAS: dict[AnalysisKind, tuple[str, ...]] = {
    AnalysisKind.SUMMARIZE_REPO: (
        "languages",
        "frameworks",
        "entry_points",
        "endpoints",
        "depends_on",
        "database_access",
        "observability",
    ),
    AnalysisKind.SUMMARIZE_TERRAFORM: (
        "resources",
        "networking",
        "managed_databases",
        "modules",
    ),
}


@dataclass(frozen=True)
class Case:
    """One public repository, and what it is here to exercise."""

    name: str
    repo_url: str
    kind: AnalysisKind
    why: str
    commit: str | None = None


CASES = [
    Case(
        name="full-stack-fastapi-template",
        repo_url="https://github.com/fastapi/full-stack-fastapi-template",
        kind=AnalysisKind.SUMMARIZE_REPO,
        why="A service, not a library: HTTP routes, a database through SQLModel and "
        "Alembic, Docker Compose, and Sentry — one repository that should fill every area.",
    ),
    Case(
        name="terraform-aws-rds",
        repo_url="https://github.com/terraform-aws-modules/terraform-aws-rds",
        kind=AnalysisKind.SUMMARIZE_TERRAFORM,
        why="Managed databases and their sizing are the areas F3 points its "
        "recommendations at, and this module is nothing else.",
    ),
]


@dataclass
class Score:
    case: str
    kind: str
    commit: str
    succeeded: bool
    error: str | None = None
    filled: list[str] = field(default_factory=list)
    unknown: dict[str, str] = field(default_factory=dict)

    @property
    def coverage(self) -> str:
        total = len(self.filled) + len(self.unknown)
        return f"{len(self.filled)}/{total}" if total else "0/0"


def resolve_commit(repo_url: str) -> str:
    """The remote's current tip. Cloning needs a commit, and inventing one is not an option."""
    completed = subprocess.run(
        ["git", "ls-remote", repo_url, "HEAD"], capture_output=True, text=True, check=True
    )
    return completed.stdout.split()[0]


def build_request(case: Case) -> AnalysisRequest:
    commit = case.commit or resolve_commit(case.repo_url)
    if case.kind is AnalysisKind.SUMMARIZE_REPO:
        return repo_summary_request(case.repo_url, commit)
    return terraform_summary_request(case.repo_url, commit)


def score(case: Case, request: AnalysisRequest, result: AnalysisResult) -> Score:
    outcome = Score(
        case=case.name,
        kind=case.kind.value,
        commit=request.commit,
        succeeded=result.succeeded,
        error=result.error,
    )
    if result.result is None:
        return outcome
    for area in AREAS[case.kind]:
        value = getattr(result.result, area)
        if isinstance(value, Unknown):
            outcome.unknown[area] = value.reason
        else:
            outcome.filled.append(area)
    return outcome


async def evaluate(case: Case) -> tuple[Score, AnalysisResult]:
    request = build_request(case)
    result = await LocalAnalysisRunner(ENTRYPOINT).run(request)
    return score(case, request, result), result


async def main() -> int:
    print("This clones public repositories and calls the analysis tier. It spends money.\n")
    scores = []
    for case in CASES:
        print(f"── {case.name}: {case.why}")
        outcome, result = await evaluate(case)
        scores.append(outcome)
        if result.result is not None:
            print(result.result.model_dump_json(indent=2))
        print(json.dumps(asdict(outcome), indent=2))

    print("\ncoverage")
    for outcome in scores:
        print(f"  {outcome.case}: {outcome.coverage}")
        for area, reason in outcome.unknown.items():
            print(f"    ? {area}: {reason}")
    return 0 if all(outcome.succeeded for outcome in scores) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
