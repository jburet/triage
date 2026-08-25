"""Fetching the tree an analysis reads (plan M7 phase 3.2).

Two of these drive a real ``git`` against a local remote, because the property
that matters — the analysis reads the commit it was given and cannot be steered
to another — is a property of git's own resolution, and a scripted shell would
only prove that the argv is what this module wrote. No network: the remote is a
directory.
"""

from __future__ import annotations

import base64

from tests.conftest import an_analysis_request, git
from triage.analysis.clone import CompletedCommand, clone, credential_env, run_command
from triage.schemas.analysis import AnalysisKind


class ScriptedGit:
    """Stands in for the shell, recording every argv and environment."""

    def __init__(self, *, fails_at: str = "") -> None:
        self.fails_at = fails_at
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    async def __call__(self, argv, *, cwd=None, env=None, timeout):
        self.calls.append((tuple(argv), dict(env or {})))
        if self.fails_at and self.fails_at in argv:
            return CompletedCommand(tuple(argv), 128, "", "fatal: no such object")
        return CompletedCommand(tuple(argv), 0, "", "")

    def argv_for(self, verb: str) -> tuple[str, ...]:
        return next(argv for argv, _ in self.calls if verb in argv)


async def test_the_tree_is_the_commit_that_was_asked_for(tmp_path, remote):
    """Not the tip: production runs a build, and the tip is what merged since."""
    url, older, newer = remote
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    failure = await clone(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, repo_url=url, commit=older),
        workspace,
        timeout=60.0,
    )

    assert failure is None
    assert git("rev-parse", "HEAD", cwd=workspace) == older
    assert (workspace / "old.py").exists()
    assert not (workspace / "new.py").exists(), f"the clone drifted to {newer}"


async def test_a_ref_the_request_did_not_name_is_refused(tmp_path, remote):
    """A branch name is not a commit, and resolving one would read a tree nobody chose."""
    url, _older, _newer = remote
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    failure = await clone(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, repo_url=url, commit="main"),
        workspace,
        timeout=60.0,
    )

    assert failure is not None
    assert "main" in failure
    assert not (workspace / "old.py").exists()


async def test_a_commit_the_remote_does_not_have_is_a_stated_failure(tmp_path, remote):
    url, _older, _newer = remote
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    absent = "0" * 40

    failure = await clone(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, repo_url=url, commit=absent),
        workspace,
        timeout=60.0,
    )

    assert failure is not None
    assert absent in failure


async def test_the_fetch_names_the_requested_commits_and_nothing_else(tmp_path):
    commands = ScriptedGit()

    await clone(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS),
        tmp_path,
        command=commands,
        timeout=60.0,
    )

    fetch = commands.argv_for("fetch")
    assert fetch[-1:] == ("9f2c1ab",)
    assert not {"--tags", "--all", "HEAD", "main", "master"} & set(fetch)
    assert commands.argv_for("checkout")[-1] == "9f2c1ab"


async def test_the_token_reaches_git_through_the_environment_and_never_the_argv(tmp_path):
    """A token in the remote URL is a token in every error message the failure quotes."""
    commands = ScriptedGit()

    await clone(
        an_analysis_request(AnalysisKind.CODE_ANALYSIS, repo_url="github.com/zeenea/datacatalog"),
        tmp_path,
        command=commands,
        timeout=60.0,
        token="ghp_secret",
    )

    header = base64.b64encode(b"x-access-token:ghp_secret").decode()
    for argv, env in commands.calls:
        assert not [word for word in argv if "ghp_secret" in word]
        assert header in env["GIT_CONFIG_VALUE_0"]
        assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"


def test_a_remote_that_is_not_https_gets_no_credentials():
    assert credential_env("ghp_secret", "file:///tmp/origin") == {}
    assert credential_env("", "https://github.com/zeenea/datacatalog") == {}


async def test_run_command_reports_a_timeout_rather_than_hanging():
    completed = await run_command(["sleep", "5"], timeout=0.2)

    assert not completed.ok
    assert "timed out" in completed.stderr
