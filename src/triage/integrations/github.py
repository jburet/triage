"""Read-only GitHub, for the one question the cartography graph asks it.

The incremental refresh has to know what a merge changed before it decides
whether to pay for an analysis (ADR-0006, ADR-0015). It cannot find out by
cloning: architecture §7 gives GitHub egress to the analysis Job, not to the
graph, and the whole point of the Job is that repository content is read inside
the sandbox. A comparison is different from a clone — it returns filenames, not
code — so it is a plain read the graph may make itself.

Deliberately one method. The architecture's rule is "MCP servers when they
exist, Python tools otherwise" and a GitHub MCP server does exist, but reaching
one for a single unauthenticated-shaped read would put an MCP runtime in the
graph's path for no gain; when M3 needs richer GitHub reads, this protocol is
the seam to reconsider it behind.

:class:`GitHubRestClient` is **unverified against a live server** — the request
shape is covered by ``tests/integration/test_github_client.py``, whether the
configured token can see a given repository is what a staging run is for.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"

COMPARE_FILE_CAP = 300
"""GitHub returns at most this many files and does not flag that it truncated."""

_REPO_PATTERN = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


class GitHubError(RuntimeError):
    """A GitHub read failed, or returned something that cannot be trusted as complete."""


def repo_path(repo_url: str) -> str:
    """``owner/name`` from any of the spellings config.yaml and webhooks use."""
    match = _REPO_PATTERN.search(repo_url.strip())
    if match is None:
        raise GitHubError(f"{repo_url!r} is not a GitHub repository URL")
    return f"{match['owner']}/{match['name']}"


class GitHubClient(Protocol):
    async def changed_paths(self, repo_url: str, *, base: str, head: str) -> list[str]:
        """Paths changed between two commits, repository-relative."""
        ...


class GitHubRestClient:
    """Compares two commits over the REST API. Read-only; it holds no write scope."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = API_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=api_url, timeout=timeout)
        # Per request, not on the client: a test supplying its own transport must
        # still send what production sends, or the auth header is never exercised.
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def changed_paths(self, repo_url: str, *, base: str, head: str) -> list[str]:
        path = repo_path(repo_url)
        response = await self._client.get(
            f"/repos/{path}/compare/{base}...{head}", headers=self._headers
        )
        if response.status_code >= 400:
            raise GitHubError(f"comparing {base}...{head} in {path} returned {_explain(response)}")

        files = response.json().get("files") or []
        if len(files) >= COMPARE_FILE_CAP:
            raise GitHubError(
                f"comparing {base}...{head} in {path} hit GitHub's {COMPARE_FILE_CAP}-file "
                f"cap, so the list of changes is incomplete"
            )
        return [str(item["filename"]) for item in files]


def _explain(response: httpx.Response) -> str:
    try:
        message = response.json().get("message")
    except ValueError:
        message = None
    return f"{response.status_code}: {message or response.text[:200] or '<empty response>'}"


@dataclass
class FakeGitHubClient:
    """Canned comparisons keyed by repository URL, recording what was asked.

    An unconfigured repository is an assertion, not an empty diff: an empty diff
    means "nothing changed", which would silently send a test down the
    carry-forward branch and prove nothing. Dry run supplies ``error`` instead,
    so a run with no GitHub access re-summarises rather than assuming.
    """

    changed: Mapping[str, Sequence[str]] = field(default_factory=dict)
    error: Exception | None = None
    comparisons: list[tuple[str, str, str]] = field(default_factory=list)

    async def changed_paths(self, repo_url: str, *, base: str, head: str) -> list[str]:
        self.comparisons.append((repo_url, base, head))
        if self.error is not None:
            raise self.error
        try:
            return list(self.changed[repo_url])
        except KeyError:
            raise AssertionError(
                f"FakeGitHubClient has no comparison configured for {repo_url!r}"
            ) from None


def dry_run_github() -> FakeGitHubClient:
    """Dry run makes no GitHub call, and says so rather than claiming nothing changed."""
    return FakeGitHubClient(
        error=GitHubError("dry run: no comparison was made, so nothing is known to be unchanged")
    )
