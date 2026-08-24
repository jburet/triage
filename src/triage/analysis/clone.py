"""Fetching the one tree an analysis reads (ADR-0009, ADR-0020).

Shared by the two places that need a clone and must not disagree about what it
is: the host runner, which clones and then runs the entrypoint in the result,
and the image, which is handed a workspace and clones into it itself because a
Job's container is all there is on the far side of the boundary.

Three properties are the reason this is a module and not four lines of shell.
The fetch names the commits the request named and nothing else — no branch, no
tags, no ``HEAD`` — because ADR-0020 spent its argument on which commit is read
and a fallback here would quietly undo it. A ref the remote will not give is a
*stated* failure, so the analysis says it could not look rather than answering
about whatever tree happened to be there. And the credential travels in the
environment: a token in the remote URL is a token in every error message the
failure quotes back to a developer.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from triage.integrations.github import clone_url
from triage.schemas.analysis import AnalysisRequest

CLONE_DEPTH = 1
"""ADR-0009: the analyses read a tree at a commit, not a history."""

OBJECT_NAME = re.compile(r"[0-9a-f]{7,40}")
"""What the ladder in ADR-0020 produces. Anything else is a name, and a name resolves."""

_STDERR_TAIL = 500


@dataclass(frozen=True)
class CompletedCommand:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    """A shell, narrow enough that a test can be one."""

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> CompletedCommand: ...


async def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float,
) -> CompletedCommand:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return CompletedCommand(tuple(argv), 124, "", f"timed out after {timeout:.0f}s")
    return CompletedCommand(
        tuple(argv),
        process.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


def tail(text: str) -> str:
    return text.strip()[-_STDERR_TAIL:] or "no output"


def credential_env(token: str, remote: str) -> dict[str, str]:
    """Git's own config, passed as environment rather than as arguments.

    ``GIT_CONFIG_*`` rather than ``git -c``: the latter puts the header on the
    command line, where the process list and every quoted failure can read it.
    Scoped to the remote's own origin so a redirect cannot carry it elsewhere.
    """
    parts = urlsplit(remote)
    if not token or parts.scheme != "https" or not parts.netloc:
        return {}
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.https://{parts.netloc}/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {header}",
    }


def clone_steps(request: AnalysisRequest, directory: Path, *, git: str = "git") -> list[list[str]]:
    here = [git, "-C", str(directory)]
    fetch = [*here, "fetch", "--depth", str(CLONE_DEPTH)]
    if request.base_commit:
        fetch.append("--filter=blob:none")
    return [
        [git, "init", "--quiet", str(directory)],
        [*here, "remote", "add", "origin", clone_url(request.repo_url)],
        [*fetch, "origin", *request.commits],
        # --detach, so that a ref which slipped the shape check below still
        # cannot become a branch git chose to track.
        [*here, "checkout", "--quiet", "--detach", request.commit],
    ]


async def clone(
    request: AnalysisRequest,
    directory: Path,
    *,
    command: CommandRunner = run_command,
    timeout: float,
    git: str = "git",
    token: str = "",
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Fetch ``request.commit`` into ``directory``. Returns why it could not be.

    A ref that is not an object name is refused before any network happens.
    ``git remote add`` writes a refspec, so ``fetch origin main`` populates
    ``refs/remotes/origin/main`` and ``checkout main`` then follows it — the
    tree read would be whatever that branch points at *now*, which is the one
    thing ADR-0020 exists to stop. The request's commit comes off a ladder that
    yields object names, or off a hypothesis a model wrote.
    """
    for ref in request.commits:
        if not OBJECT_NAME.fullmatch(ref):
            return (
                f"{ref!r} is not a commit: an analysis reads the object name it was "
                f"given, never a name it resolved itself"
            )
    remote = clone_url(request.repo_url)
    # The whole environment, not just the credential: `subprocess` resolves the
    # program against the PATH of the environment it is handed, so a two-entry
    # one turns "git" into "no such file".
    environment = {
        **(dict(env) if env is not None else os.environ),
        **credential_env(token, remote),
    }
    for argv in clone_steps(request, directory, git=git):
        completed = await command(argv, cwd=directory, env=environment, timeout=timeout)
        if not completed.ok:
            return f"clone failed at `{' '.join(argv)}`: {tail(completed.stderr)}"
    return None
