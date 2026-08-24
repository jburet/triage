"""Routing each hypothesis to its analysis branch (architecture §2.1).

The cause type decides the branch and the branch decides the question, so this
node is a lookup plus the one thing that genuinely needs the map: where the code
is. A repository and a commit are *resolved*, never assumed — an analysis run
against the wrong commit answers a question nobody asked, and one run against a
guessed repository is worse than one not run at all. When resolution fails the
hypothesis still comes back, carrying a failed result that says what was missing,
because the diagnosis has to be able to state that it could not look.

The branches run concurrently: they are independent Jobs against different
repositories, and the wall clock of a diagnosis is the slowest one, not the sum.
"""

import asyncio

from langchain_core.runnables import RunnableConfig

from triage.config import RepoKind
from triage.graphs.state import AnalysisState, Investigated
from triage.runtime import Deps, deps_from_runnable_config
from triage.schemas.analysis import AnalysisKind, AnalysisRequest, AnalysisResult
from triage.schemas.hypothesis import CauseType, Hypothesis
from triage.scope import deployed_repo

KIND_FOR_CAUSE: dict[CauseType, AnalysisKind] = {
    CauseType.APP: AnalysisKind.CODE_ANALYSIS,
    CauseType.INFRA: AnalysisKind.IAC_ANALYSIS,
    CauseType.DEPLOYMENT: AnalysisKind.DIFF_ANALYSIS,
}

QUESTION_FOR_CAUSE: dict[CauseType, str] = {
    CauseType.APP: (
        "Does the code at this commit explain it, and in which files and functions exactly?"
    ),
    CauseType.INFRA: (
        "Does the infrastructure code at this commit explain it, and which resource or module?"
    ),
    CauseType.DEPLOYMENT: (
        "Does the difference between these two commits explain it, and which change specifically?"
    ),
}


def _question(hypothesis: Hypothesis) -> str:
    return (
        f"Service {hypothesis.service}. Hypothesis under test: {hypothesis.description}\n"
        f"{QUESTION_FOR_CAUSE[hypothesis.cause_type]} Answer only from what the tree shows."
    )


def _terraform_repo(deps: Deps, team: str) -> str | None:
    """The Terraform repository to read an infrastructure hypothesis in.

    The owning team's, when it declares one; otherwise the only one there is. Two
    undeclared candidates is an ambiguity, and picking either would send the
    developer to a repository that does not provision this service.
    """
    terraform = [repo for repo in deps.config.repos if repo.kind is RepoKind.TERRAFORM]
    owned = [repo for repo in terraform if repo.team == team]
    if len(owned) == 1:
        return owned[0].url
    if not owned and len(terraform) == 1:
        return terraform[0].url
    return None


async def _plan(state: AnalysisState, deps: Deps, hypothesis: Hypothesis) -> Investigated:
    """Resolve where this hypothesis is analysed, or say why it cannot be."""
    if hypothesis.cause_type is CauseType.DEPENDENCY:
        return Investigated(hypothesis=hypothesis)
    kind = KIND_FOR_CAUSE[hypothesis.cause_type]

    if hypothesis.cause_type is CauseType.INFRA:
        repo_url = _terraform_repo(deps, state.get("team", ""))
        if repo_url is None:
            return Investigated(
                hypothesis=hypothesis,
                result=AnalysisResult.failed(
                    kind,
                    f"no single Terraform repository is declared for team "
                    f"{state.get('team', '')!r} in config.yaml, so there is nothing to read",
                ),
            )
        commit = hypothesis.commit or await deps.repo.last_summarised_commit(repo_url)
        if commit is None:
            return Investigated(
                hypothesis=hypothesis,
                repo_url=repo_url,
                result=AnalysisResult.failed(
                    kind, f"no commit is known for {repo_url}: it has never been summarised"
                ),
            )
        return Investigated(
            hypothesis=hypothesis,
            repo_url=repo_url,
            commit=commit,
            result=None,
        )

    deployment = await deployed_repo(deps.config, deps.repo, hypothesis.service)
    repo_url, mapped_commit = deployment.repo_url, deployment.commit
    if repo_url is None:
        return Investigated(
            hypothesis=hypothesis,
            result=AnalysisResult.failed(
                kind,
                f"service {hypothesis.service!r} is not in the system map and no "
                f"repository in config.yaml declares it under `serves`, so no "
                f"repository could be resolved for it",
            ),
        )

    commit = hypothesis.commit or mapped_commit
    if commit is None:
        return Investigated(
            hypothesis=hypothesis,
            repo_url=repo_url,
            result=AnalysisResult.failed(
                kind, f"no deployed commit is known for {hypothesis.service}"
            ),
        )
    base_commit = None
    if hypothesis.cause_type is CauseType.DEPLOYMENT:
        base_commit = hypothesis.base_commit or mapped_commit
        if not base_commit or base_commit == commit:
            return Investigated(
                hypothesis=hypothesis,
                repo_url=repo_url,
                commit=commit,
                result=AnalysisResult.failed(
                    kind,
                    f"no earlier commit is known for {hypothesis.service} to diff {commit} against",
                ),
            )
    return Investigated(
        hypothesis=hypothesis,
        repo_url=repo_url,
        commit=commit,
        base_commit=base_commit,
        commit_source=deployment.commit_source,
        result=None,
    )


async def _run(deps: Deps, planned: Investigated) -> Investigated:
    hypothesis = planned.hypothesis
    if hypothesis.cause_type is CauseType.DEPENDENCY or planned.result is not None:
        return planned
    assert planned.repo_url is not None
    assert planned.commit is not None
    request = AnalysisRequest(
        kind=KIND_FOR_CAUSE[hypothesis.cause_type],
        repo_url=planned.repo_url,
        commit=planned.commit,
        base_commit=planned.base_commit,
        question=_question(hypothesis),
    )
    return planned.model_copy(update={"result": await deps.runner.run(request)})


async def run_analyses(state: AnalysisState, config: RunnableConfig | None = None) -> AnalysisState:
    deps = deps_from_runnable_config(config)
    selected = state.get("selected", [])
    planned = [await _plan(state, deps, hypothesis) for hypothesis in selected]
    investigated = await asyncio.gather(*(_run(deps, item) for item in planned))
    return {"investigated": list(investigated)}
