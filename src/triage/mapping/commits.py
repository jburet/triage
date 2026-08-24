"""The commit a workload is running, when its image tag is not one.

Phase 2 could get as far as the digest and no further: `platform` ships images
tagged `501`, a build number, and reading seven hexadecimal digits as a commit
is how an analysis ends up at a commit that does not exist. But that build
number *is* a tag in GitHub, and GitHub will say which commit it names — so the
fact is one read away, and without it every analysis runs at "the last commit F0
summarised" and apologises for it.

Preference is stated rather than emergent: the image tag when it carries the
commit outright, then the tag as GitHub resolves it, then the default branch —
which is *not* the deployed commit and must never be presented as one, hence
:data:`CommitSource` on the entry rather than a bare string.
"""

from __future__ import annotations

from datetime import datetime

from triage.config import Config
from triage.integrations.github import GitHubClient, GitHubError
from triage.mapping.images import split_reference
from triage.schemas.common import Confidence, MaybeUnknown, Unknown
from triage.schemas.system_map import CommitSource, MappingSource, WorkloadEntry

CONFIDENCE_CAP: dict[CommitSource, Confidence] = {CommitSource.DEFAULT_BRANCH: Confidence.MEDIUM}
"""How sure a diagnosis may be about code read at a commit from this source.

Production runs the default branch in essentially every case, and the case where
it does not is the one whose incident matters — a customer pinned to an older
build, a hotfix branch, a rollback. The failure there is quiet: the analysis
reads real code at a real commit and answers confidently about a tree the tenant
is not running. So the cap, and :func:`commit_caveat`, are the two things that
keep that visible in the ticket.
"""


def commit_caveat(source: CommitSource | None, repo_url: str | None) -> str | None:
    """What a diagnosis reading a commit from this source has to state, if anything."""
    if source is not CommitSource.DEFAULT_BRANCH:
        return None
    return (
        f"The analysis read the default branch of {repo_url or 'the repository'} as it "
        f"stood, because no build was identifiable from the image the service is running; "
        f"that this is the deployed code is not established."
    )


def _undeclared(entry: WorkloadEntry) -> Unknown:
    """The image names a repository; config.yaml names a *GitHub* repository."""
    return Unknown(
        reason=(
            f"the image says {entry.service} runs {entry.repository}, but no repository in "
            f"config.yaml is named {entry.repository!r}, so there is no GitHub remote to "
            f"resolve its build against"
        )
    )


async def _resolved(
    github: GitHubClient, repo_url: str, tag: str | None, at: datetime | None
) -> tuple[MaybeUnknown, CommitSource, datetime | None]:
    """The declared tag spelling, then the default branch — and no third guess.

    A tag invented by guessing points somewhere specific and wrong, so no second
    tag spelling is tried. The default branch is not a guess of that kind:
    production runs it in essentially every case, and the alternative on this
    path is not a better commit, it is no commit at all. What it is *not* is the
    deployed commit, which is why the source is recorded rather than smoothed
    over (2.16).
    """
    if tag is not None:
        commit = await github.commit_for_tag(repo_url, tag)
        if commit is not None:
            return commit, CommitSource.GITHUB_TAG, None
    return (
        await github.default_branch_commit(repo_url, at=at),
        CommitSource.DEFAULT_BRANCH,
        at,
    )


async def with_deployed_commit(
    github: GitHubClient,
    config: Config,
    entry: WorkloadEntry,
    *,
    at: datetime | None = None,
) -> WorkloadEntry:
    """The same entry, with whatever GitHub can add about which commit it runs.

    A pattern mapping is left alone: it observed no build, so there is nothing to
    resolve and a tag lookup would be about some other service's image. ``at`` is
    when the incident fired: a diagnosis of Tuesday's outage read against
    Thursday's default branch is a different repository.
    """
    if entry.source is not MappingSource.IMAGE or entry.image is None:
        return entry
    if isinstance(entry.deployed_commit, str):
        return entry
    repo = config.repo_named(entry.repository)
    if repo is None:
        return entry.model_copy(update={"deployed_commit": _undeclared(entry)})

    _, tag, _ = split_reference(entry.image)
    try:
        commit, source, read_at = await _resolved(github, repo.url, repo.github_tag(tag), at)
    except GitHubError as error:
        return entry.model_copy(
            update={
                "deployed_commit": Unknown(
                    reason=f"GitHub would not say which commit {entry.service} runs: {error}"
                )
            }
        )
    return entry.model_copy(
        update={"deployed_commit": commit, "commit_source": source, "commit_read_at": read_at}
    )
