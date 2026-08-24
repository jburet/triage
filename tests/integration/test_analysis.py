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
from triage.analysis.entrypoint import unanswerable
from triage.analysis.runner import FakeAnalysisRunner
from triage.config import Config, Repo, RepoKind
from triage.db.repo import InMemoryRepository
from triage.graphs.analysis import build_graph
from triage.integrations.github import FakeGitHubClient
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
from triage.schemas.system_map import MappingSource


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


async def test_the_investigation_records_which_of_the_three_answered_for_the_repository(
    config: Config,
):
    """A repository the running image named and one a glob suggested are different
    facts, and the run is the only place that still knows which it was."""
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-api",
            repository="payments-api",
            repo_url="github.com/org/payments-api",
        )
    )
    deps = build_deps(config, repo=repo)

    state = await run(deps, hypotheses=[a_hypothesis()])

    assert state["investigated"][0].mapping_source is MappingSource.IMAGE


async def test_a_repository_only_the_system_map_knew_is_recorded_as_the_map_s_answer(
    config: Config,
):
    deps = build_deps(config, repo=mapped(a_service_entry()))

    state = await run(deps, hypotheses=[a_hypothesis()])

    assert state["investigated"][0].mapping_source is MappingSource.MAP


async def test_a_repository_only_a_name_pattern_matched_is_never_a_high_confidence_diagnosis(
    config: Config,
):
    """`serves: ["payments-*"]` is a hand-maintained guess about naming, and the
    commit behind it is the last one F0 summarised for the repository — a fact
    about the repository, not about the build this service is running."""
    deps = build_deps(
        config, repo=mapped(a_service_entry()), syntheses=[a_synthesis(confidence="high")]
    )

    diagnosis = (
        await run(deps, hypotheses=[a_hypothesis(service="payments-worker", commit=None)])
    )["diagnosis"]

    assert diagnosis.confidence is Confidence.MEDIUM
    assert "name pattern" in diagnosis.confidence_rationale
    assert "last commit summarised" in diagnosis.confidence_rationale


async def test_a_workload_row_the_derivation_only_guessed_carries_the_same_caveat(
    config: Config,
):
    """2.4's fallback writes a row for a service that emitted no image event. It is
    the patterns wearing a row, and it must not read as the image-derived mapping
    it is stored next to."""
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-api",
            repository="payments-api",
            repo_url="github.com/org/payments-api",
            deployed_commit="9f2c1ab",
            image=None,
            image_digest=None,
            source="pattern",
        )
    )
    deps = build_deps(config, repo=repo, syntheses=[a_synthesis(confidence="high")])

    diagnosis = (await run(deps, hypotheses=[a_hypothesis()]))["diagnosis"]

    assert diagnosis.confidence is Confidence.MEDIUM
    assert "name pattern" in diagnosis.confidence_rationale


async def test_a_repository_the_map_named_carries_no_pattern_caveat(config: Config):
    deps = build_deps(
        config, repo=mapped(a_service_entry()), syntheses=[a_synthesis(confidence="high")]
    )

    diagnosis = (await run(deps, hypotheses=[a_hypothesis()]))["diagnosis"]

    assert diagnosis.confidence is Confidence.HIGH
    assert "name pattern" not in diagnosis.confidence_rationale


async def test_an_iac_repository_is_read_at_its_default_branch(config: Config):
    """ADR-0020 refuses a default-branch commit as *the deployed* one, which is
    right for an application: the running build is observable and a guess would
    displace it. Terraform is not deployed by image and has no build to observe,
    so the default branch is not a substitute for the answer — it is the answer,
    and refusing it cost the 2026-08-24 live run its only infrastructure analysis.
    """
    deps = build_deps(
        config,
        repo=InMemoryRepository(),
        github=FakeGitHubClient(branch_commits={"github.com/org/infra": "abc1234"}),
    )

    result = await run(deps, hypotheses=[a_hypothesis(CauseType.INFRA, commit=None)])

    request = deps.runner.requests_for(AnalysisKind.IAC_ANALYSIS)[0]
    assert request.commit == "abc1234"
    assert result["investigated"][0].result.status is AnalysisStatus.SUCCEEDED


async def test_an_iac_repository_github_cannot_answer_for_still_says_so(config: Config):
    deps = build_deps(config, repo=InMemoryRepository(), github=FakeGitHubClient())

    result = await run(deps, hypotheses=[a_hypothesis(CauseType.INFRA, commit=None)])

    assert deps.runner.requests_for(AnalysisKind.IAC_ANALYSIS) == []
    assert result["investigated"][0].result is not None


async def test_a_deployment_hypothesis_is_an_unknown_because_diff_analysis_has_no_entrypoint(
    config: Config,
):
    """M7 3.4. The kind the image refuses has to arrive as an unknown a developer
    can read, not as a silent gap — so the canned result is the image's own
    refusal, and implementing diff_analysis is what makes this test say so."""
    refusal = unanswerable(AnalysisKind.DIFF_ANALYSIS)
    assert refusal is not None, "diff_analysis has an entrypoint now; M7 3.4 said it does not"
    deps = build_deps(
        config,
        repo=mapped(a_service_entry()),
        runner=FakeAnalysisRunner(results={AnalysisKind.DIFF_ANALYSIS: refusal}),
        syntheses=[a_synthesis(confidence="high")],
    )

    diagnosis = (
        await run(
            deps,
            hypotheses=[
                a_hypothesis(CauseType.DEPLOYMENT, commit="bbbbbbb", base_commit="1111111")
            ],
        )
    )["diagnosis"]

    assert diagnosis.confidence is Confidence.MEDIUM
    assert "diff_analysis" in diagnosis.unknowns[0].why_unresolved
