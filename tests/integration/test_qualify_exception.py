"""The ``qualify_exception`` node, through the deps it is given (M8 4.1-4.3).

Offline: the model is `FakeLLM` keyed on `Qualification`, GitHub is the fake that
answers only the tags a test configured, and no Datadog call happens here at all.
"""

import json
from datetime import UTC, datetime, timedelta

from tests.conftest import a_qualification, a_workload, build_deps, declaring, run_config
from triage.db.repo import InMemoryRepository
from triage.integrations.github import FakeGitHubClient
from triage.nodes.qualify_exception import qualify_exception
from triage.schemas.collection import (
    Collector,
    CollectorResult,
    CollectorStatus,
    Qualification,
)
from triage.schemas.common import Feature, TimeWindow
from triage.schemas.errors import (
    ErrorCollection,
    ErrorGroup,
    ErrorTrack,
    Novelty,
    Reconstruction,
)
from triage.schemas.hypothesis import CauseType

NOW = datetime(2026, 8, 25, 5, 35, tzinfo=UTC)
REPO = "github.com/org/payments-api"
AN_APP_CAUSE = {
    "cause_type": "app",
    "service": "payments-us",
    "description": "The lookup returns None and is treated as fatal.",
    "rank_score": 0.8,
}


def a_group(**overrides: object) -> ErrorGroup:
    base: dict[str, object] = {
        "key": "EntityNotFoundException|OdbClient.scala|$anonfun$load$6|payments-api",
        "error_type": "zeenea.commons.exceptions.EntityNotFoundException",
        "file_path": "zeenea.repository.orientdb.OdbClient.scala",
        "function_name": "$anonfun$load$6",
        "repository": "payments-api",
        "repo_url": REPO,
        "team": "payments",
        "track": ErrorTrack.TRACE,
        "novelty": Novelty.NEW,
        "services": {"payments-eu": 40, "payments-us": 5869},
        "occurrences": 5909,
        "sample_message": "Entity not found: load_contact_by_id",
        "first_seen": NOW - timedelta(days=30),
        "last_seen": NOW,
    }
    base.update(overrides)
    return ErrorGroup.model_validate(base)


def a_collection(group: ErrorGroup) -> ErrorCollection:
    """The measured shape: the queries ran and Datadog had discarded the evidence."""
    return ErrorCollection(
        group_key=group.key,
        window=TimeWindow(start=NOW - timedelta(hours=1), end=NOW),
        reconstruction=Reconstruction(narrow="a", broad="b", control="c"),
        claimed_occurrences=group.occurrences,
        results=[
            CollectorResult(
                collector=Collector.ERROR_SPANS,
                query="a",
                status=CollectorStatus.SAMPLED_AWAY,
                detail="counted 5,909 and returned none of them",
            )
        ],
    )


async def a_repository() -> InMemoryRepository:
    repo = InMemoryRepository()
    await repo.upsert_workload(
        a_workload(
            service="payments-us",
            repository="payments-api",
            repo_url=REPO,
            deployed_commit="cafe123",
        )
    )
    return repo


async def qualify(group: ErrorGroup, **over: object) -> dict:
    deps = build_deps(
        declaring(REPO, team="payments"),
        repo=await a_repository(),
        github=over.pop("github", None) or FakeGitHubClient(),
        qualifications=over.pop("qualifications", None),
    )
    state = {"group": group, "collection": a_collection(group)}
    return dict(await qualify_exception(state, run_config(deps)))  # type: ignore[arg-type]


async def test_the_app_hypothesis_carries_the_paths_derived_from_the_class_name():
    """M8 4.1 — the whole point: a class name is not a path in a tree."""
    result = await qualify(
        a_group(),
        qualifications=[a_qualification(AN_APP_CAUSE)],
    )

    app = next(h for h in result["hypotheses"] if h.cause_type is CauseType.APP)
    assert app.paths == [
        "src/main/scala/zeenea/repository/orientdb/OdbClient.scala",
        "zeenea/repository/orientdb/OdbClient.scala",
    ]
    assert result["feature"] is Feature.F2
    assert result["team"] == "payments"


async def test_a_claimed_version_is_the_commit_every_code_hypothesis_reads():
    result = await qualify(
        a_group(first_seen_version="501"),
        github=FakeGitHubClient(tags={(REPO, "501"): "deadbee"}),
        qualifications=[a_qualification(AN_APP_CAUSE)],
    )

    assert result["exception"].commit.commit == "deadbee"
    assert result["exception"].commit.claimed
    assert {h.commit for h in result["hypotheses"] if h.cause_type is CauseType.APP} == {"deadbee"}


async def test_with_no_version_the_commit_falls_back_and_the_context_says_so():
    result = await qualify(a_group())

    choice = result["exception"].commit
    assert choice.commit == "cafe123"
    assert not choice.claimed
    assert "recorded no version" in result["context"]["commit_read"]["rung"]


async def test_a_recorded_version_adds_a_deployment_hypothesis_ranked_to_be_bought():
    result = await qualify(
        a_group(first_seen_version="501", last_seen_version="514"),
        github=FakeGitHubClient(tags={(REPO, "501"): "deadbee"}),
    )

    boundary = result["hypotheses"][0]
    assert boundary.cause_type is CauseType.DEPLOYMENT
    assert "`501`" in boundary.description
    assert "`514`" in boundary.description
    assert boundary.base_commit is None


async def test_no_version_adds_no_deployment_hypothesis():
    result = await qualify(a_group())

    assert all(h.cause_type is not CauseType.DEPLOYMENT for h in result["hypotheses"])


async def test_the_prompt_carries_the_exception_as_a_tagged_json_block():
    """Inputs are model-adjacent prose; interpolating them into instructions is not done."""
    deps = build_deps(
        declaring(REPO, team="payments"),
        repo=await a_repository(),
        github=FakeGitHubClient(),
    )
    group = a_group()
    await qualify_exception(
        {"group": group, "collection": a_collection(group)},  # type: ignore[arg-type]
        run_config(deps),
    )

    prompt = deps.llm.calls_for(Qualification)[0].prompt
    block = json.loads(prompt.split("<exception>", 1)[1].split("</exception>", 1)[0])
    assert block["error_type"] == group.error_type
    assert block["method"] == "load"
    assert block["occurrences_per_service"] == group.services
