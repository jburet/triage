"""The commit a workload is running, when its image tag is not one.

Phase 2 could get as far as the digest and no further: `platform` ships images
tagged `501`, a build number, and reading seven hexadecimal digits as a commit
is how an analysis ends up at a commit that does not exist. But that build
number *is* a tag in GitHub, and GitHub will say which commit it names — so the
fact is one read away, and without it every analysis runs at "the last commit F0
summarised" and apologises for it.

Preference is stated rather than emergent: the image tag when it carries the
commit outright, then the tag as GitHub resolves it.
"""

from __future__ import annotations

from triage.config import Config
from triage.integrations.github import GitHubClient, GitHubError
from triage.mapping.images import split_reference
from triage.schemas.common import MaybeUnknown, Unknown
from triage.schemas.system_map import CommitSource, MappingSource, WorkloadEntry


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
    github: GitHubClient, repo_url: str, entry: WorkloadEntry, tag: str | None
) -> tuple[MaybeUnknown, CommitSource | None]:
    if tag is not None:
        commit = await github.commit_for_tag(repo_url, tag)
        if commit is not None:
            return commit, CommitSource.GITHUB_TAG
    return (
        Unknown(
            reason=(
                f"{entry.service} runs the image tag {tag!r}, which {repo_url} has no tag "
                f"named, so which commit it was built from is not known"
                if tag
                else f"{entry.service} runs an image with no tag, so nothing names the "
                f"commit {repo_url} was built from for it"
            )
        ),
        None,
    )


async def with_deployed_commit(
    github: GitHubClient,
    config: Config,
    entry: WorkloadEntry,
) -> WorkloadEntry:
    """The same entry, with whatever GitHub can add about which commit it runs.

    A pattern mapping is left alone: it observed no build, so there is nothing to
    resolve and a tag lookup would be about some other service's image.
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
        commit, source = await _resolved(github, repo.url, entry, tag)
    except GitHubError as error:
        return entry.model_copy(
            update={
                "deployed_commit": Unknown(
                    reason=f"GitHub would not say which commit {entry.service} runs: {error}"
                )
            }
        )
    return entry.model_copy(update={"deployed_commit": commit, "commit_source": source})
