"""Summarising a repository, offline (plan M2 phase 2.1, 2.2).

The entrypoint is the only part of Triage that runs inside the sandbox, so it is
also the only part no integration test can reach. What these tests pin is the
contract on either side of it: what the model is shown, and what shape comes
back out — including the failures, which must be stated results rather than
tracebacks nobody sees.
"""

import json
from pathlib import Path

import pytest

from tests.conftest import (
    a_repo_summary,
    a_terraform_summary,
    an_analysis_request,
    some_findings,
)
from triage.analysis.context import ContextBudget
from triage.analysis.entrypoint import analyse, report, run
from triage.llm import FakeLLM, StructuredOutputError
from triage.schemas.analysis import AnalysisFindings, AnalysisKind, AnalysisResult
from triage.schemas.common import Unknown
from triage.schemas.system_map import RepoSummary, TerraformSummary


class FailingLLM:
    """A tier that answered with nothing the schema admits."""

    async def call(self, tier, prompt, schema):
        raise StructuredOutputError(tier, schema)


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def application_repo(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname = 'payments-api'\n")
    write(tmp_path, "src/payments/main.py", "app = FastAPI()\n")
    write(tmp_path, "src/payments/models.py", "class Payment: ...\n")
    return tmp_path


@pytest.fixture
def terraform_repo(tmp_path):
    write(
        tmp_path,
        "main.tf",
        'resource "aws_db_instance" "primary" {\n  instance_class = "db.r6g.large"\n}\n',
    )
    write(tmp_path, "terraform.tfstate", '{"resources": [{"instance": "db-abc123"}]}\n')
    return tmp_path


def tagged(prompt: str, tag: str) -> dict:
    return json.loads(prompt.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0])


async def test_an_application_repo_is_summarised_into_every_area(application_repo):
    llm = FakeLLM(responses={RepoSummary: [a_repo_summary()]})

    result = await analyse(an_analysis_request(AnalysisKind.SUMMARIZE_REPO), application_repo, llm)

    assert result.succeeded
    summary = result.result
    assert isinstance(summary, RepoSummary)
    for area in (
        "languages",
        "frameworks",
        "entry_points",
        "endpoints",
        "depends_on",
        "database_access",
        "observability",
    ):
        value = getattr(summary, area)
        assert isinstance(value, Unknown) or value, f"{area} is neither filled nor an Unknown"


async def test_a_terraform_repo_is_summarised_from_code_alone(terraform_repo):
    llm = FakeLLM(responses={TerraformSummary: [a_terraform_summary()]})

    result = await analyse(
        an_analysis_request(AnalysisKind.SUMMARIZE_TERRAFORM), terraform_repo, llm
    )

    assert result.succeeded
    assert isinstance(result.result, TerraformSummary)
    prompt = llm.calls_for(TerraformSummary)[0].prompt
    assert "db-abc123" not in prompt
    assert [f["path"] for f in tagged(prompt, "repository")["files"]] == ["main.tf"]


async def test_the_model_is_shown_the_tree_and_the_files_that_decide_the_answer(application_repo):
    llm = FakeLLM(responses={RepoSummary: [a_repo_summary()]})

    await analyse(an_analysis_request(AnalysisKind.SUMMARIZE_REPO), application_repo, llm)

    repository = tagged(llm.calls_for(RepoSummary)[0].prompt, "repository")
    assert "src/payments/main.py" in repository["tree"]
    assert "pyproject.toml" in [item["path"] for item in repository["files"]]


async def test_what_was_not_read_reaches_the_model(application_repo):
    """A gap the model is not told about is a gap it fills in with something plausible."""
    llm = FakeLLM(responses={RepoSummary: [a_repo_summary()]})

    await analyse(
        an_analysis_request(AnalysisKind.SUMMARIZE_REPO),
        application_repo,
        llm,
        budget=ContextBudget(max_files=1),
    )

    assert tagged(llm.calls_for(RepoSummary)[0].prompt, "repository")["not_examined"]


async def test_the_summary_is_produced_by_the_analysis_tier(application_repo):
    llm = FakeLLM(responses={RepoSummary: [a_repo_summary()]})

    await analyse(an_analysis_request(AnalysisKind.SUMMARIZE_REPO), application_repo, llm)

    assert [call.tier for call in llm.calls] == ["analysis"]


async def test_instructions_precede_the_repository(application_repo):
    llm = FakeLLM(responses={RepoSummary: [a_repo_summary()]})

    await analyse(an_analysis_request(AnalysisKind.SUMMARIZE_REPO), application_repo, llm)

    prompt = llm.calls_for(RepoSummary)[0].prompt
    assert prompt.index("Never invent") < prompt.index("<repository>")
    assert tagged(prompt, "request")["kind"] == "summarize_repo"


async def test_an_area_the_model_could_not_determine_stays_unknown(application_repo):
    partial = a_repo_summary(
        endpoints=Unknown(reason="No router or route table appears in the files read.")
    )
    llm = FakeLLM(responses={RepoSummary: [partial]})

    result = await analyse(an_analysis_request(AnalysisKind.SUMMARIZE_REPO), application_repo, llm)

    assert isinstance(result.result, RepoSummary)
    assert isinstance(result.result.endpoints, Unknown)


async def test_a_kind_with_no_analyser_is_a_stated_failure(application_repo):
    """diff_analysis reads a patch between two commits, which is not this gather."""
    llm = FakeLLM(responses={})

    result = await analyse(an_analysis_request(AnalysisKind.DIFF_ANALYSIS), application_repo, llm)

    assert not result.succeeded
    assert "diff_analysis" in (result.error or "")


@pytest.mark.parametrize(
    ("kind", "repository", "expected_file"),
    [
        (AnalysisKind.CODE_ANALYSIS, "application_repo", "src/payments/main.py"),
        (AnalysisKind.IAC_ANALYSIS, "terraform_repo", "main.tf"),
    ],
)
async def test_an_investigation_answers_from_the_files_its_kind_selects(
    kind, repository, expected_file, request
):
    """The question is the same shape either way; which files are opened is not."""
    llm = FakeLLM(responses={AnalysisFindings: [some_findings()]})

    result = await analyse(an_analysis_request(kind), request.getfixturevalue(repository), llm)

    assert result.succeeded
    assert isinstance(result.result, AnalysisFindings)
    shown = tagged(llm.calls[0].prompt, "repository")
    assert expected_file in [item["path"] for item in shown["files"]]


async def test_an_investigation_opens_the_files_the_mapping_says_define_the_workload(tmp_path):
    """M6 3.2: the chart holds the probe timeout, and the profile would have spent
    the budget on the repository's other modules first."""
    write(tmp_path, "modules/eks/main.tf", "module {}\n")
    write(tmp_path, "helm/zeenea-platform/templates/statefulset.yaml", "kind: StatefulSet\n")
    llm = FakeLLM(responses={AnalysisFindings: [some_findings()]})

    await analyse(
        an_analysis_request(
            AnalysisKind.IAC_ANALYSIS, paths=["helm/zeenea-platform/templates/statefulset.yaml"]
        ),
        tmp_path,
        llm,
        budget=ContextBudget(max_files=1),
    )

    shown = tagged(llm.calls[0].prompt, "repository")
    assert [item["path"] for item in shown["files"]] == [
        "helm/zeenea-platform/templates/statefulset.yaml"
    ]


async def test_a_tier_that_returns_nothing_parsable_is_a_stated_failure(application_repo):
    result = await analyse(
        an_analysis_request(AnalysisKind.SUMMARIZE_REPO),
        application_repo,
        FailingLLM(),  # type: ignore[arg-type]
    )

    assert not result.succeeded
    assert "summarize_repo" in (result.error or "")


def test_the_entrypoint_emits_what_the_local_runner_reads_back(capsys):
    """stdout is the payload, and a failure is an exit code — the runner's contract."""
    summary = a_repo_summary()
    kind = AnalysisKind.SUMMARIZE_REPO

    code = report(AnalysisResult(kind=kind, status="succeeded", result=summary))
    out = capsys.readouterr().out

    assert code == 0
    assert AnalysisResult.from_payload(kind, json.loads(out)).result == summary


def test_a_failed_analysis_exits_non_zero_and_says_why(capsys):
    code = report(AnalysisResult.failed(AnalysisKind.SUMMARIZE_REPO, "clone was empty"))
    captured = capsys.readouterr()

    assert code == 1
    assert "clone was empty" in captured.err
    assert not captured.out.strip()


async def test_the_image_clones_the_workspace_it_is_handed_and_answers_from_it(tmp_path, remote):
    """M7 3.2. A Job's container is all there is on the far side of the boundary,
    so nothing has cloned for it — the host runner's arrangement does not reach here."""
    url, older, _newer = remote
    llm = FakeLLM(responses={AnalysisFindings: [some_findings()]})
    workspace = tmp_path / "workspace"

    result = await run(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, repo_url=url, commit=older),
        llm,
        workspace=workspace,
    )

    assert result.succeeded
    assert (workspace / "old.py").exists()
    assert "old.py" in tagged(llm.calls[0].prompt, "repository")["tree"]


async def test_a_clone_that_failed_is_a_stated_failure_and_costs_no_model_call(tmp_path, remote):
    url, _older, _newer = remote
    llm = FakeLLM(responses={})

    result = await run(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, repo_url=url, commit="0" * 40),
        llm,
        workspace=tmp_path / "workspace",
    )

    assert not result.succeeded
    assert "0" * 40 in (result.error or "")
    assert llm.calls == []


async def test_with_no_workspace_the_working_directory_is_the_tree(application_repo, monkeypatch):
    """The host runner clones first and runs the entrypoint inside the result."""
    llm = FakeLLM(responses={RepoSummary: [a_repo_summary()]})
    monkeypatch.chdir(application_repo)

    result = await run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO), llm, workspace=None)

    assert result.succeeded


async def test_a_kind_with_no_analyser_fails_before_anything_is_cloned(tmp_path, remote):
    """M7 3.4. diff_analysis is still not implemented, and the honest failure names
    the kind — a clone that ran first would fail for its own reasons instead, and
    would have paid for a tree nothing was going to read."""
    url, older, _newer = remote
    workspace = tmp_path / "workspace"

    result = await run(
        an_analysis_request(
            AnalysisKind.DIFF_ANALYSIS, repo_url=url, commit=older, base_commit=older
        ),
        FakeLLM(responses={}),
        workspace=workspace,
    )

    assert not result.succeeded
    assert "diff_analysis" in (result.error or "")
    assert not workspace.exists()
