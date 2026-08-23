"""The host runner (plan M2 phase 1.2).

What is worth pinning here is not the analysis — that happens inside the
entrypoint — but the tree it is handed and the mess it leaves behind. A clone
that survives a failed run fills a developer's disk one analysis at a time, and
the failure that produced it is the case nobody tests.
"""

import json
from pathlib import Path

import pytest

from tests.conftest import a_repo_summary, an_analysis_request
from triage.analysis.runner import CompletedCommand, LocalAnalysisRunner
from triage.schemas.analysis import AnalysisKind, AnalysisStatus

ENTRYPOINT = ("triage-analysis",)


class ScriptedCommands:
    """Stands in for the shell: records every argv, answers from a script."""

    def __init__(self, *, stdout: str = "", entrypoint_returncode: int = 0, git_fails_at: str = ""):
        self.stdout = stdout
        self.entrypoint_returncode = entrypoint_returncode
        self.git_fails_at = git_fails_at
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    async def __call__(self, argv, *, cwd=None, env=None, timeout):
        self.calls.append((tuple(argv), cwd))
        if argv[0] != "git":
            return CompletedCommand(tuple(argv), self.entrypoint_returncode, self.stdout, "boom")
        if self.git_fails_at and self.git_fails_at in argv:
            return CompletedCommand(tuple(argv), 128, "", "fatal: could not read from remote")
        return CompletedCommand(tuple(argv), 0, "", "")

    def argv_for(self, verb: str) -> tuple[str, ...]:
        return next(argv for argv, _ in self.calls if verb in argv)

    @property
    def directories(self) -> set[Path]:
        return {cwd for _, cwd in self.calls if cwd is not None}


def a_summary_json() -> str:
    return json.dumps(a_repo_summary().model_dump(mode="json"))


async def test_it_clones_one_commit_shallowly_and_runs_the_entrypoint_there():
    commands = ScriptedCommands(stdout=a_summary_json())
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    result = await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))

    assert result.succeeded
    fetch = commands.argv_for("fetch")
    assert "--depth" in fetch
    assert fetch[fetch.index("--depth") + 1] == "1"
    assert "9f2c1ab" in fetch
    assert "--filter=blob:none" not in fetch

    (entrypoint_argv, entrypoint_cwd) = commands.calls[-1]
    assert entrypoint_argv == ENTRYPOINT
    assert entrypoint_cwd is not None
    assert commands.directories == {entrypoint_cwd}


async def test_a_diff_fetches_both_commits_without_their_blobs():
    """ADR-0009: depth 1, and --filter=blob:none when two commits are needed."""
    commands = ScriptedCommands(
        stdout=json.dumps({"answer": "x", "findings": [], "confidence": "low"})
    )
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    await runner.run(an_analysis_request(AnalysisKind.DIFF_ANALYSIS))

    fetch = commands.argv_for("fetch")
    assert "--filter=blob:none" in fetch
    assert fetch[-2:] == ("9f2c1ab", "1111111")


async def test_the_clone_is_gone_when_the_entrypoint_fails():
    commands = ScriptedCommands(entrypoint_returncode=1)
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    result = await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))

    assert result.status is AnalysisStatus.FAILED
    assert "exited 1" in (result.error or "")
    (directory,) = commands.directories
    assert not directory.exists()


async def test_the_clone_is_gone_when_the_entrypoint_raises():
    class Exploding(ScriptedCommands):
        async def __call__(self, argv, *, cwd=None, env=None, timeout):
            await super().__call__(argv, cwd=cwd, env=env, timeout=timeout)
            if argv[0] != "git":
                raise OSError("no such executable")
            return CompletedCommand(tuple(argv), 0, "", "")

    commands = Exploding()
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    with pytest.raises(OSError, match="no such executable"):
        await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))

    (directory,) = commands.directories
    assert not directory.exists()


async def test_the_clone_is_gone_and_the_entrypoint_never_ran_when_the_fetch_fails():
    commands = ScriptedCommands(git_fails_at="fetch")
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    result = await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))

    assert result.status is AnalysisStatus.FAILED
    assert "could not read from remote" in (result.error or "")
    assert all(argv[0] == "git" for argv, _ in commands.calls)
    (directory,) = commands.directories
    assert not directory.exists()


async def test_output_that_is_not_json_is_a_failed_result():
    commands = ScriptedCommands(stdout="I had a look around and it seems fine")
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    result = await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))

    assert result.status is AnalysisStatus.FAILED
    assert "JSON" in (result.error or "")


async def test_output_that_is_json_but_not_the_kinds_schema_is_a_failed_result():
    """Plan 1.4, on the host runner."""
    commands = ScriptedCommands(stdout=json.dumps({"repo_url": "github.com/org/payments-api"}))
    runner = LocalAnalysisRunner(ENTRYPOINT, command=commands)

    result = await runner.run(an_analysis_request(AnalysisKind.SUMMARIZE_REPO))

    assert result.status is AnalysisStatus.FAILED
    assert result.result is None
    assert "summarize_repo" in (result.error or "")
    assert "RepoSummary" in (result.error or "")


async def test_the_request_reaches_the_entrypoint_as_its_environment():
    commands = ScriptedCommands(stdout=a_summary_json())
    seen: dict[str, str] = {}

    async def capturing(argv, *, cwd=None, env=None, timeout):
        if argv[0] != "git" and env:
            seen.update(env)
        return await commands(argv, cwd=cwd, env=env, timeout=timeout)

    runner = LocalAnalysisRunner(ENTRYPOINT, command=capturing)
    request = an_analysis_request(AnalysisKind.SUMMARIZE_REPO)
    await runner.run(request)

    assert json.loads(seen["TRIAGE_ANALYSIS_REQUEST"])["commit"] == request.commit
