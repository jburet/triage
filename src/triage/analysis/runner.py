"""Ways to run one analysis (architecture §7, ADR-0009).

One method, three implementations, chosen by where the code is running: a fake in
tests, a subprocess on the host for development and evals, and a gVisor
Kubernetes Job in production. The node that submits the request cannot tell them
apart, which is the point — an analysis is expensive and sandboxed, and no graph
should have to know how.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import structlog

from triage.analysis.jobs import (
    JOB_TIMEOUT_SECONDS,
    REQUEST_ENV,
    JobApi,
    JobApiError,
    job_manifest,
    job_name,
)
from triage.config import AnalysisJobConfig
from triage.db.repo import AnalysisResultRecord, TriageRepository
from triage.schemas.analysis import AnalysisKind, AnalysisRequest, AnalysisResult, AnalysisStatus

log = structlog.get_logger(__name__)

CLONE_DEPTH = 1
"""ADR-0009: the analyses read a tree at a commit, not a history."""

DEFAULT_POLL_SECONDS = 5.0

_STDERR_TAIL = 500


class AnalysisRunner(Protocol):
    async def run(self, request: AnalysisRequest) -> AnalysisResult: ...


def dry_run_result(request: AnalysisRequest) -> AnalysisResult:
    """Dry run submits no Job, and says so instead of inventing a summary."""
    return AnalysisResult.failed(
        request.kind, "dry run: no analysis Job was submitted, so nothing was analysed"
    )


Canned = "AnalysisResult | Sequence[AnalysisResult]"


@dataclass
class FakeAnalysisRunner:
    """Canned results keyed by kind, recording what was asked.

    A sequence is consumed one call at a time and its last element repeats, the
    same rule as :class:`~triage.llm.FakeLLM`. An unconfigured kind is an
    assertion rather than a failed result: a test that forgot to arrange one
    would otherwise pass down the failure branch and prove nothing. Production
    dry-run supplies ``default`` instead.
    """

    results: Mapping[AnalysisKind, AnalysisResult | Sequence[AnalysisResult]] = field(
        default_factory=dict
    )
    default: Callable[[AnalysisRequest], AnalysisResult] | None = None
    requests: list[AnalysisRequest] = field(default_factory=list)
    _cursor: dict[AnalysisKind, int] = field(default_factory=dict)

    async def run(self, request: AnalysisRequest) -> AnalysisResult:
        self.requests.append(request)
        canned = self.results.get(request.kind)
        if canned is None:
            if self.default is not None:
                return self.default(request)
            raise AssertionError(
                f"FakeAnalysisRunner has no result configured for {request.kind.value}"
            )
        if isinstance(canned, AnalysisResult):
            return canned
        index = min(self._cursor.get(request.kind, 0), len(canned) - 1)
        self._cursor[request.kind] = index + 1
        return canned[index]

    def requests_for(self, kind: AnalysisKind) -> list[AnalysisRequest]:
        return [request for request in self.requests if request.kind is kind]


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


def _tail(text: str) -> str:
    return text.strip()[-_STDERR_TAIL:] or "no output"


class LocalAnalysisRunner:
    """Runs the analysis entrypoint on this host, in a throwaway clone.

    For development and evals. The clone is the same shallow one the Job makes,
    so the entrypoint sees the tree it will see in production; what it does not
    get is the sandbox, which is exactly why this must never be the production
    runner. The directory is removed in a ``finally`` — a clone that outlives a
    failed run fills a disk one analysis at a time, and failure is the case that
    otherwise goes untested.
    """

    def __init__(
        self,
        entrypoint: Sequence[str],
        *,
        command: CommandRunner = run_command,
        workdir: Path | None = None,
        timeout: float = JOB_TIMEOUT_SECONDS,
        git: str = "git",
    ) -> None:
        self._entrypoint = tuple(entrypoint)
        self._command = command
        self._workdir = workdir
        self._timeout = timeout
        self._git = git

    async def run(self, request: AnalysisRequest) -> AnalysisResult:
        directory = Path(
            tempfile.mkdtemp(
                prefix="triage-analysis-", dir=str(self._workdir) if self._workdir else None
            )
        )
        try:
            failure = await self._clone(request, directory)
            if failure is not None:
                return failure
            return await self._analyse(request, directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def _clone_steps(self, request: AnalysisRequest, directory: Path) -> list[list[str]]:
        here = [self._git, "-C", str(directory)]
        fetch = [*here, "fetch", "--depth", str(CLONE_DEPTH)]
        if request.base_commit:
            fetch.append("--filter=blob:none")
        return [
            [self._git, "init", "--quiet", str(directory)],
            [*here, "remote", "add", "origin", request.repo_url],
            [*fetch, "origin", *request.commits],
            [*here, "checkout", "--quiet", request.commit],
        ]

    async def _clone(self, request: AnalysisRequest, directory: Path) -> AnalysisResult | None:
        for argv in self._clone_steps(request, directory):
            completed = await self._command(argv, cwd=directory, timeout=self._timeout)
            if not completed.ok:
                return AnalysisResult.failed(
                    request.kind,
                    f"clone failed at `{' '.join(argv)}`: {_tail(completed.stderr)}",
                )
        return None

    async def _analyse(self, request: AnalysisRequest, directory: Path) -> AnalysisResult:
        completed = await self._command(
            self._entrypoint,
            cwd=directory,
            env={**os.environ, REQUEST_ENV: request.model_dump_json()},
            timeout=self._timeout,
        )
        if not completed.ok:
            return AnalysisResult.failed(
                request.kind,
                f"analysis entrypoint exited {completed.returncode}: {_tail(completed.stderr)}",
            )
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return AnalysisResult.failed(
                request.kind, f"analysis entrypoint did not emit JSON: {exc}"
            )
        return AnalysisResult.from_payload(request.kind, raw)


class KubernetesJobRunner:
    """Submits one sandboxed Job per analysis and reads the row it writes (ADR-0009).

    The Job has no path back to the graph, so the result channel is a single row
    in ``triage.analysis_results`` keyed by Job name, which this runner polls
    through the repository. Nothing here raises: a wedged Job, a Job that errored
    and a payload that does not validate are all *failed results*, because a
    failed analysis is a fact the diagnosis has to record — losing the whole run
    over one hypothesis is the worse outcome.

    The Job is deleted whatever happened, and a delete that fails is logged
    rather than allowed to discard a good result.
    """

    def __init__(
        self,
        jobs: JobApi,
        repo: TriageRepository,
        spec: AnalysisJobConfig,
        *,
        timeout: float = JOB_TIMEOUT_SECONDS,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jobs = jobs
        self._repo = repo
        self._spec = spec
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._clock = clock

    async def run(self, request: AnalysisRequest) -> AnalysisResult:
        name = job_name(request)
        manifest = job_manifest(request, name=name, spec=self._spec)
        try:
            await self._jobs.create(manifest)
        except JobApiError as exc:
            return AnalysisResult.failed(request.kind, f"could not submit Job {name}: {exc}")
        try:
            return await self._await_result(request, name)
        finally:
            await self._delete(name)

    async def _await_result(self, request: AnalysisRequest, name: str) -> AnalysisResult:
        started = self._clock()
        while True:
            row = await self._repo.analysis_result(name)
            if row is not None and row.status.is_terminal:
                return self._admit(request, name, row)

            status = await self._jobs.status(name)
            if status.failed:
                return AnalysisResult.failed(
                    request.kind,
                    f"Job {name} failed: {status.reason or 'no reason reported'}",
                )
            if status.succeeded:
                # The Job writes its row before exiting, so one that finished
                # without writing never will; waiting out the deadline for it
                # only delays the diagnosis.
                return AnalysisResult.failed(
                    request.kind, f"Job {name} finished but wrote no result row"
                )
            if self._clock() - started >= self._timeout:
                return AnalysisResult.failed(
                    request.kind,
                    f"Job {name} reported no result within {self._timeout:.0f}s",
                )
            await self._sleep(self._poll_interval)

    def _admit(
        self, request: AnalysisRequest, name: str, row: AnalysisResultRecord
    ) -> AnalysisResult:
        if row.kind is not request.kind:
            return AnalysisResult.failed(
                request.kind, f"Job {name} wrote a {row.kind.value} result"
            )
        if row.status is AnalysisStatus.FAILED:
            return AnalysisResult.failed(
                request.kind, row.error or "the analysis reported a failure with no message"
            )
        return AnalysisResult.from_payload(request.kind, row.result)

    async def _delete(self, name: str) -> None:
        try:
            await self._jobs.delete(name)
        except JobApiError as exc:
            log.warning("analysis_job_not_deleted", job=name, error=str(exc))
