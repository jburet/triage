"""Read-only GitHub, for the one question the cartography graph asks it.

The incremental refresh has to know what a merge changed before it decides
whether to pay for an analysis (ADR-0006, ADR-0015). It cannot find out by
cloning: architecture §7 gives GitHub egress to the analysis Job, not to the
graph, and the whole point of the Job is that repository content is read inside
the sandbox. A comparison is different from a clone — it returns filenames, not
code — so it is a plain read the graph may make itself.

M6 added two more: which commit the build a tenant is running was cut from,
because the image tag is a build number and that number is a tag here (2.6),
and which files of an IaC repository define a given workload (3.1). All are
single-shot reads of metadata, and none returns code, so the reasoning holds — the
architecture's rule is "MCP servers when they exist, Python tools otherwise" and
a GitHub MCP server does exist, but reaching one for reads of this shape would
put an MCP runtime in the graph's path for no gain. This protocol stays the seam
to reconsider it behind.

:class:`GitHubRestClient` is **unverified against a live server** — the request
shape is covered by ``tests/integration/test_github_client.py``, whether the
configured token can see a given repository is what a staging run is for.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

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

    async def commit_for_tag(self, repo_url: str, tag: str) -> str | None:
        """The commit this tag points at, or None when the repository has no such tag.

        An absent tag is an answer rather than an error: an image tag that is a
        build number is a tag in GitHub for the repositories that push one, and
        is nothing at all for the ones that do not.
        """
        ...

    async def default_branch_commit(self, repo_url: str, *, at: datetime | None = None) -> str:
        """The commit the default branch pointed at then, or at HEAD given no time."""
        ...

    async def default_branch_paths(self, repo_url: str) -> list[str]:
        """Every file the default branch has, repository-relative.

        A listing, not content: it says where a workload is defined so the
        analysis can open those files inside the sandbox (M6 3.1).
        """
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

    async def commit_for_tag(self, repo_url: str, tag: str) -> str | None:
        path = repo_path(repo_url)
        response = await self._client.get(
            f"/repos/{path}/git/ref/tags/{tag}", headers=self._headers
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(f"reading tag {tag!r} in {path} returned {_explain(response)}")
        return await self._dereferenced(path, tag, response.json().get("object") or {})

    async def _dereferenced(self, path: str, tag: str, obj: dict[str, Any]) -> str:
        """The commit behind a ref's object, through the tag object of an annotated tag.

        An annotated tag is its own object in the graph, and the ref points at
        *that*: taking its sha would hand every later analysis a ref no clone can
        check out, and the failure would look like a repository that lost a commit.
        """
        sha = obj.get("sha")
        if not isinstance(sha, str):
            raise GitHubError(f"the tag {tag!r} in {path} names no object")
        if obj.get("type") != "tag":
            return sha
        response = await self._client.get(f"/repos/{path}/git/tags/{sha}", headers=self._headers)
        if response.status_code >= 400:
            raise GitHubError(
                f"reading the annotated tag {tag!r} in {path} returned {_explain(response)}"
            )
        commit = (response.json().get("object") or {}).get("sha")
        if not isinstance(commit, str):
            raise GitHubError(f"the annotated tag {tag!r} in {path} names no commit")
        return commit

    async def default_branch_commit(self, repo_url: str, *, at: datetime | None = None) -> str:
        path = repo_path(repo_url)
        repository = await self._client.get(f"/repos/{path}", headers=self._headers)
        if repository.status_code >= 400:
            raise GitHubError(f"reading {path} returned {_explain(repository)}")
        branch = repository.json().get("default_branch")
        if not isinstance(branch, str):
            raise GitHubError(f"{path} names no default branch")

        params = {"sha": branch, "per_page": "1"}
        if at is not None:
            params["until"] = at.isoformat()
        commits = await self._client.get(
            f"/repos/{path}/commits", params=params, headers=self._headers
        )
        if commits.status_code >= 400:
            raise GitHubError(f"reading {branch} of {path} returned {_explain(commits)}")
        head = commits.json() or []
        if not head or not isinstance(head[0].get("sha"), str):
            raise GitHubError(
                f"{branch} of {path} has no commit"
                + (f" from before {at.isoformat()}" if at else "")
            )
        return str(head[0]["sha"])

    async def default_branch_paths(self, repo_url: str) -> list[str]:
        path = repo_path(repo_url)
        response = await self._client.get(
            f"/repos/{path}/git/trees/HEAD", params={"recursive": "1"}, headers=self._headers
        )
        if response.status_code >= 400:
            raise GitHubError(f"listing the files of {path} returned {_explain(response)}")
        body = response.json()
        if body.get("truncated"):
            raise GitHubError(
                f"GitHub truncated its listing of {path}, so a chart missing from it would "
                f"read as a workload that is defined nowhere"
            )
        return [str(item["path"]) for item in body.get("tree") or [] if item.get("type") == "blob"]


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
    tags: Mapping[tuple[str, str], str] = field(default_factory=dict)
    branch_commits: Mapping[str, str] = field(default_factory=dict)
    trees: Mapping[str, Sequence[str]] = field(default_factory=dict)
    error: Exception | None = None
    comparisons: list[tuple[str, str, str]] = field(default_factory=list)
    tag_lookups: list[tuple[str, str]] = field(default_factory=list)
    branch_reads: list[tuple[str, datetime | None]] = field(default_factory=list)
    tree_reads: list[str] = field(default_factory=list)

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

    async def commit_for_tag(self, repo_url: str, tag: str) -> str | None:
        """Unlike a comparison, an unconfigured tag is the real answer: no such tag."""
        self.tag_lookups.append((repo_url, tag))
        if self.error is not None:
            raise self.error
        return self.tags.get((repo_url, tag))

    async def default_branch_commit(self, repo_url: str, *, at: datetime | None = None) -> str:
        self.branch_reads.append((repo_url, at))
        if self.error is not None:
            raise self.error
        try:
            return self.branch_commits[repo_url]
        except KeyError:
            raise GitHubError(
                f"FakeGitHubClient has no default-branch commit for {repo_url!r}"
            ) from None

    async def default_branch_paths(self, repo_url: str) -> list[str]:
        """An unconfigured repository is an error: an empty tree is a chart nowhere."""
        self.tree_reads.append(repo_url)
        if self.error is not None:
            raise self.error
        try:
            return list(self.trees[repo_url])
        except KeyError:
            raise GitHubError(f"FakeGitHubClient has no file listing for {repo_url!r}") from None


def dry_run_github() -> FakeGitHubClient:
    """Dry run makes no GitHub call, and says so rather than claiming nothing changed."""
    return FakeGitHubClient(
        error=GitHubError(
            "dry run: GitHub was not read, so nothing is known to be unchanged, no tag "
            "was resolved to a commit and no repository was listed"
        )
    )


def unconfigured_github() -> FakeGitHubClient:
    """No token was set, and every read says so rather than returning a 401.

    The failure is caught per repository and turned into an Unknown (M6 2.12), so
    an unset variable would otherwise reach the mapping report as twenty identical
    "401: Bad credentials" reasons — indistinguishable from a token that exists
    and cannot see those repositories, which is a different problem with a
    different fix.
    """
    return FakeGitHubClient(
        error=GitHubError(
            "TRIAGE_GITHUB_TOKEN is unset, so no GitHub read was attempted: no comparison "
            "was made, no tag was resolved to a commit and no repository was listed"
        )
    )
