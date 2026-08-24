"""The Analysis sub-graph, end to end against fakes (M3 Phase 1, ADR-0005).

What is worth pinning here is not that a model was called — it is that the facts
around the model are the run's own: the commit a hypothesis is analysed at, the
branch each cause type takes, which hypotheses were bought and which were only
recorded, and what happens to a diagnosis that claims more than it earned.
"""

from tests.conftest import (
    a_hypothesis,
    a_service_entry,
    a_synthesis,
    a_workload,
    build_deps,
    mapped,
    run_config,
    some_findings,
)
from triage.analysis.runner import FakeAnalysisRunner
from triage.config import Config, Repo, RepoKind
from triage.db.repo import InMemoryRepository
from triage.graphs.analysis import build_graph
from triage.runtime import Deps
from triage.schemas import (
    AnalysisKind,
    AnalysisResult,
    CauseType,
    Confidence,
    Feature,
    Unknown,
)
from triage.schemas.analysis import AnalysisStatus


def graph():
    return build_graph().compile()


async def run(deps: Deps, **state: object) -> dict:
    base: dict[str, object] = {
        "feature": Feature.F1,
        "service": "payments-api",
        "team": "payments",
        "context": {"alert": "container memory above 95% for 15 minutes"},
    }
    base.update(state)
    return await graph().ainvoke(base, config=run_config(deps))  # type: ignore[arg-type]


async def test_diagnosis_locates_the_hypothesis_and_carries_its_finding(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    result = await run(deps, hypotheses=[a_hypothesis(commit="deadbee")])

    diagnosis = result["diagnosis"]
    assert diagnosis.location.repo == "github.com/org/payments-api"
    assert diagnosis.location.commit == "deadbee"
    assert deps.runner.requests_for(AnalysisKind.CODE_ANALYSIS)[0].commit == "deadbee"
    finding = some_findings().findings[0]
    assert any(finding.statement in item.description for item in diagnosis.evidence)


async def test_each_cause_type_takes_its_own_branch(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    await run(
        deps,
        hypotheses=[
            a_hypothesis(CauseType.APP, commit="aaaaaaa"),
            a_hypothesis(CauseType.INFRA),
            a_hypothesis(CauseType.DEPLOYMENT, commit="bbbbbbb", base_commit="1111111"),
        ],
    )

    assert len(deps.runner.requests_for(AnalysisKind.CODE_ANALYSIS)) == 1
    iac = deps.runner.requests_for(AnalysisKind.IAC_ANALYSIS)
    assert [request.repo_url for request in iac] == ["github.com/org/infra"]
    diff = deps.runner.requests_for(AnalysisKind.DIFF_ANALYSIS)
    assert (diff[0].commit, diff[0].base_commit) == ("bbbbbbb", "1111111")


async def test_a_dependency_cause_calls_no_runner(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    result = await run(deps, hypotheses=[a_hypothesis(CauseType.DEPENDENCY, rank_score=0.8)])

    assert deps.runner.requests == []
    assert result["investigated"][0].result is None


async def test_only_the_ranked_hypotheses_above_the_floor_are_analysed(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))
    ranks = [0.9, 0.8, 0.7, 0.6, 0.1]

    result = await run(
        deps,
        hypotheses=[
            a_hypothesis(rank_score=rank, commit=f"{index}aaaaaa")
            for index, rank in enumerate(ranks)
        ],
    )

    assert config.analysis.max_hypotheses == 3
    assert [item.rank_score for item in result["selected"]] == [0.9, 0.8, 0.7]
    assert len(deps.runner.requests) == 3
    assert [item.hypothesis.rank_score for item in result["deferred"]] == [0.6, 0.1]


async def test_something_is_always_analysed_even_below_the_floor(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    result = await run(
        deps, hypotheses=[a_hypothesis(rank_score=0.2), a_hypothesis(rank_score=0.1)]
    )

    assert [item.rank_score for item in result["selected"]] == [0.2]
    assert len(deps.runner.requests) == 1


async def test_hypotheses_not_analysed_are_ruled_out_with_their_reason(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    result = await run(
        deps,
        hypotheses=[
            a_hypothesis(rank_score=0.9, description="Unbounded cache in the API."),
            a_hypothesis(rank_score=0.1, description="A noisy neighbour on the node."),
        ],
    )

    ruled_out = result["diagnosis"].ruled_out
    assert [item.hypothesis for item in ruled_out] == ["A noisy neighbour on the node."]
    assert "below the 0.30 floor" in ruled_out[0].why


async def test_a_rejected_hypothesis_keeps_the_analysis_finding_as_its_reason(config: Config):
    deps = build_deps(
        config,
        repo=mapped(a_service_entry()),
        syntheses=[
            a_synthesis(
                ruled_out=[
                    {
                        "hypothesis": "A connection-pool leak.",
                        "why": "The pool size was flat across the window.",
                    }
                ]
            )
        ],
    )

    result = await run(deps, hypotheses=[a_hypothesis()])

    assert result["diagnosis"].ruled_out[0].why == "The pool size was flat across the window."


async def test_a_failed_analysis_becomes_an_unknown_and_caps_confidence(config: Config):
    deps = build_deps(
        config,
        repo=mapped(a_service_entry()),
        runner=FakeAnalysisRunner(
            results={
                AnalysisKind.CODE_ANALYSIS: AnalysisResult.failed(
                    AnalysisKind.CODE_ANALYSIS, "the Job reported no result within 900s"
                )
            }
        ),
        syntheses=[a_synthesis(confidence="high")],
    )

    diagnosis = (await run(deps, hypotheses=[a_hypothesis(description="Unbounded cache.")]))[
        "diagnosis"
    ]

    assert diagnosis.confidence is Confidence.MEDIUM
    unknown = diagnosis.unknowns[0]
    assert "Unbounded cache." in unknown.question
    assert "no result within 900s" in unknown.why_unresolved


async def test_a_service_outside_the_system_map_fails_that_analysis_only(config: Config):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    result = await run(deps, hypotheses=[a_hypothesis(service="ghost-api")])

    assert deps.runner.requests == []
    assert "not in the system map" in result["investigated"][0].result.error
    assert isinstance(result["diagnosis"].location.repo, Unknown)


async def test_an_unearned_confidence_is_retried_with_the_error(config: Config):
    deps = build_deps(
        config,
        repo=mapped(a_service_entry()),
        syntheses=[
            a_synthesis(confidence="high", chosen_hypothesis=None),
            a_synthesis(confidence="medium"),
        ],
    )

    result = await run(deps, hypotheses=[a_hypothesis()])

    assert result["synthesis_attempts"] == 2
    assert result["diagnosis"].confidence is Confidence.MEDIUM
    second = deps.llm.calls[-1].prompt
    assert "rejected_draft" in second
    assert "confidence 'high'" in second


async def test_a_synthesis_that_cannot_be_fixed_is_degraded_to_low(config: Config):
    unearned = a_synthesis(confidence="high", evidence=[], chosen_hypothesis=None)
    deps = build_deps(config, repo=mapped(a_service_entry()), syntheses=[unearned, unearned])

    result = await run(deps, hypotheses=[a_hypothesis()])

    diagnosis = result["diagnosis"]
    assert diagnosis.confidence is Confidence.LOW
    assert "No checkable evidence" in diagnosis.evidence[0].description


async def test_a_tenant_instance_is_analysed_in_the_repository_declared_as_serving_it(
    config: Config,
):
    """plt-merck-qa is one customer's instance of a platform, not its own repository.

    The system map is keyed on the name a repository says it deploys as, so no
    tenant will ever be in it; without this the whole class of alert resolves to
    "not in the system map" and nothing is ever read.
    """
    repo = mapped(
        a_service_entry(
            "platform",
            repo_url="github.com/org/platform",
            team="platform",
            source_commit="abc1234",
        )
    )
    declared = config.model_copy(
        update={
            "repos": [
                *config.repos,
                Repo(
                    url="github.com/org/platform",
                    team="platform",
                    kind=RepoKind.APPLICATION,
                    serves=["plt-*"],
                ),
            ]
        }
    )
    deps = build_deps(declared, repo=repo)

    await run(
        deps,
        hypotheses=[a_hypothesis(service="plt-merck-qa", commit=None)],
        team="platform",
    )

    request = deps.runner.requests[0]
    assert request.repo_url == "github.com/org/platform"
    assert request.commit == "abc1234"


async def test_an_infrastructure_hypothesis_reads_where_the_mapping_says_the_workload_is(
    config: Config,
):
    """M6 3.2: on 2026-08-23 the repository was right and the files were not — the
    probe timeouts are in the chart, and the selection read `*.tf`."""
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-api",
            iac_repo="platform-infra",
            iac_repo_url="github.com/zeenea/platform-infra",
            iac_paths=["helm/zeenea-platform/values.yaml"],
        )
    )
    deps = build_deps(config, repo=repo)

    await run(deps, hypotheses=[a_hypothesis(CauseType.INFRA)])

    request = deps.runner.requests_for(AnalysisKind.IAC_ANALYSIS)[0]
    assert request.repo_url == "github.com/zeenea/platform-infra"
    assert request.paths == ["helm/zeenea-platform/values.yaml"]


async def test_a_service_with_no_mapped_chart_still_reads_the_teams_terraform_repository(
    config: Config,
):
    """The M3 behaviour, kept: a workload nothing has mapped is analysed by glob."""
    deps = build_deps(config, repo=mapped(a_service_entry()))

    await run(deps, hypotheses=[a_hypothesis(CauseType.INFRA)])

    request = deps.runner.requests_for(AnalysisKind.IAC_ANALYSIS)[0]
    assert request.repo_url == "github.com/org/infra"
    assert request.paths == []


async def test_a_value_the_tenant_overrides_reaches_the_diagnosis_as_the_unknown_it_is(
    config: Config,
):
    """M6 3.4: the chart's 1s is the chart's. What this customer's StatefulSet was
    given is a per-tenant override the analysis never saw, and the synthesis has to
    be told that rather than handed a number to quote."""
    unreadable = some_findings(
        configured_values=[
            {
                "setting": "readinessProbe.timeoutSeconds",
                "chart_default": "1, in helm/zeenea-platform/values.yaml",
                "tenant_value": {
                    "unknown": True,
                    "reason": "this tenant's overrides are not in this repository",
                },
            }
        ]
    )
    deps = build_deps(
        config,
        repo=mapped(a_service_entry()),
        runner=FakeAnalysisRunner(
            results={
                AnalysisKind.CODE_ANALYSIS: AnalysisResult(
                    kind=AnalysisKind.CODE_ANALYSIS,
                    status=AnalysisStatus.SUCCEEDED,
                    result=unreadable,
                )
            }
        ),
    )

    await run(deps, hypotheses=[a_hypothesis()])

    prompt = deps.llm.calls[-1].prompt
    assert "this tenant's overrides are not in this repository" in prompt
    assert "readinessProbe.timeoutSeconds" in prompt


async def test_a_commit_read_off_the_default_branch_is_never_presented_as_the_deployed_one(
    config: Config,
):
    """Production runs the default branch in essentially every case, and the case
    where it does not is the one whose incident matters — a customer pinned to an
    older build, a hotfix branch, a rollback. The analysis still runs; what it may
    not do is claim it read the code this tenant is running."""
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-api",
            repository="payments-api",
            repo_url="github.com/org/payments-api",
            deployed_commit="9f2c1ab",
            commit_source="default_branch",
        )
    )
    deps = build_deps(config, repo=repo, syntheses=[a_synthesis(confidence="high")])

    diagnosis = (await run(deps, hypotheses=[a_hypothesis()]))["diagnosis"]

    assert diagnosis.confidence is Confidence.MEDIUM
    assert "default branch" in diagnosis.confidence_rationale
    assert "no build was identifiable" in diagnosis.confidence_rationale


async def test_a_commit_the_image_named_carries_no_such_caveat(config: Config):
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-api",
            repository="payments-api",
            repo_url="github.com/org/payments-api",
            deployed_commit="9f2c1ab",
            commit_source="image_tag",
        )
    )
    deps = build_deps(config, repo=repo, syntheses=[a_synthesis(confidence="high")])

    diagnosis = (await run(deps, hypotheses=[a_hypothesis()]))["diagnosis"]

    assert diagnosis.confidence is Confidence.HIGH
    assert "default branch" not in diagnosis.confidence_rationale
