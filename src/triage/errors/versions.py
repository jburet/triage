"""Which commit an exception's version names, and what a deployment means (M8 4.2, 4.3).

Error Tracking records the application version an exception was **first seen on**,
and that is a better question than "what is this service running now": the report
asks what the code looked like when the defect appeared. So when a repository
carries a tag for that version, that tag's commit is what the analysis reads, and
the fallback to the service map's commit is labelled as a fallback — the two must
not read alike (ADR-0019, ADR-0020).

The catch is measured, and it is the whole shape of this module. On 2026-08-25
``first_seen_version`` came back blank on 15 of 15 issues in the reference hour
and on 16 of 202 over a week. The version path is therefore the minority one, and
everything here has to work well when there is no version at all.

The deployment hypothesis (4.3) is built for the same reason and comes back
empty-handed on purpose. A version the exception first appeared on says something
changed in the release before it — but nothing Triage reads names that release.
Datadog does not, and no tag listing is asked for. So the hypothesis is raised,
names both versions the issue *did* record, and carries no base commit, which
makes the diff a stated failure that reaches the report as an open question
rather than as silence (ADR-0014). ``diff_analysis`` has no entrypoint either
(M7 3.4), so it would have been a stated failure regardless; when both are fixed
the fix is a base commit here and nothing else.
"""

from __future__ import annotations

from triage.config import Config
from triage.db.repo import TriageRepository
from triage.integrations.github import GitHubClient, GitHubError
from triage.schemas.errors import CommitChoice, ErrorGroup
from triage.schemas.hypothesis import CauseType, Hypothesis
from triage.scope import deployed_repo

DEPLOYMENT_RANK = 0.9
"""High enough to be bought, and deliberately so.

A recorded version boundary is the one dated fact in an F2 run — the collection
is usually empty (ADR-0027) and every cause the model proposes is read off the
issue's own fields. The analysis it buys costs nothing today, because it is
refused for want of a base commit before any Job is started, and that refusal is
the point: the report has to say the diff was never read (ADR-0014). Ranked first
in the list as well, so a tie with a model cause does not lose it the slot."""


def loudest_service(group: ErrorGroup) -> str:
    """The service to resolve the group against: the one raising it most.

    A group spans tenants of the same repository (ADR-0026), so any of them
    resolves to the same code; the loudest is chosen so the choice is stable and
    the one named in the report is the one a reader would have looked at.
    """
    if not group.services:
        return ""
    return max(sorted(group.services), key=lambda service: group.services[service])


async def commit_for_group(
    github: GitHubClient,
    config: Config,
    repository: TriageRepository,
    group: ErrorGroup,
) -> CommitChoice:
    """The version's commit when a repository claims it, and the map's when none does."""
    version = group.first_seen_version
    declared = config.repo_by_url(group.repo_url) if group.repo_url else None
    if version and declared is not None:
        tag = declared.github_tag(version) or version
        try:
            commit = await github.commit_for_tag(declared.url, tag)
        except GitHubError as error:
            commit = None
            note = f"GitHub would not resolve it: {error}"
        else:
            note = f"no tag `{tag}` exists in {declared.url}"
        if commit is not None:
            return CommitChoice(
                commit=commit,
                version=version,
                claimed=True,
                rung=(
                    f"the exception was first seen on version `{version}`, and the tag "
                    f"`{tag}` in {declared.url} points at this commit — this is the code as "
                    f"it stood when the defect appeared"
                ),
            )
    else:
        note = (
            "Error Tracking recorded no version for this exception"
            if not version
            else f"no repository in config.yaml is declared at {group.repo_url}"
        )

    deployment = await deployed_repo(config, repository, loudest_service(group))
    return CommitChoice(
        commit=deployment.commit,
        version=version,
        claimed=False,
        rung=(
            f"nothing claims the version this exception was first seen on ({note}), so the "
            f"commit read is the one the service map has for "
            f"`{loudest_service(group)}` — what the workload is running, not the build the "
            f"defect entered at"
        ),
    )


def deployment_hypothesis(group: ErrorGroup, choice: CommitChoice) -> Hypothesis | None:
    """A release boundary, when the issue recorded one — and nothing when it did not."""
    first = group.first_seen_version
    if not first:
        return None
    last = group.last_seen_version
    seen = (
        f"first appears on version `{first}` and was last seen on `{last}`"
        if last and last != first
        else f"first appears on version `{first}` and has been seen on no later one"
    )
    return Hypothesis(
        cause_type=CauseType.DEPLOYMENT,
        service=loudest_service(group),
        commit=choice.commit if choice.claimed else None,
        base_commit=None,
        description=(
            f"{group.error_type} {seen}, so whatever went out in `{first}` introduced it. "
            f"The release before `{first}` is not recorded by Error Tracking and Triage "
            f"reads no tag listing, so there is no earlier commit to diff `{first}` against."
        ),
        rank_score=DEPLOYMENT_RANK,
    )
