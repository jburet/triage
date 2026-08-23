"""One service, from what the cluster reported to the repository that serves it.

A rule, not a model call: everything it joins is already structured — the image
names its repository, the seed names the tenancy and the deployer, config.yaml
names the owner. What it refuses to do is guess. An image the seed does not name
is a stated failure carrying the image, never the nearest repository name by
edit distance, because a mapping that is wrong sends every later analysis into a
tree this service does not run and says nothing about having done so.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from triage.config import Config, RepoKind
from triage.mapping.images import ObservedImage, commit_in_tag, latest_image
from triage.mapping.resolve import naming_conflict
from triage.mapping.seed import seed_for
from triage.schemas.common import Unknown
from triage.schemas.system_map import (
    Derivation,
    MappingOutcome,
    MappingSource,
    SeedEntry,
    WorkloadEntry,
)


def _commit_unknown(image: ObservedImage) -> Unknown:
    """Found the image, not the commit — which is a different failure from finding neither.

    The mapping still holds: the analysis reads the right repository, but at the
    last commit F0 summarised rather than at what this tenant is running, and the
    diagnosis has to say so.
    """
    tag = f"its tag {image.tag!r} is not a commit" if image.tag else "it carries no tag"
    return Unknown(
        reason=(
            f"the image {image.reference} was found, but {tag} and no registry metadata "
            f"was read, so which commit this service is running is not known"
        )
    )


def _from_image(
    config: Config, seed: Sequence[SeedEntry], service: str, image: ObservedImage
) -> Derivation:
    declared = seed_for(list(seed), image.repository)
    if declared is None:
        return Derivation(
            service=service,
            outcome=MappingOutcome.UNRESOLVED_IMAGE,
            reason=(
                f"{service} runs the image {image.repository!r} ({image.reference}), which no "
                f"repository in the seed is named after — the seed is out of date, or this "
                f"image is not built from a repository Triage knows"
            ),
        )
    conflict = naming_conflict(declared, service)
    if conflict is not None:
        return Derivation(service=service, outcome=MappingOutcome.CONFLICT, reason=conflict)

    commit = commit_in_tag(image.tag)
    repo = config.repo_named(declared.repository)
    iac_repo = declared.iac_repo
    iac = config.repo_named(iac_repo) if iac_repo else None
    return Derivation(
        service=service,
        outcome=MappingOutcome.MAPPED,
        reason=(
            f"{service} runs {image.repository} at {image.digest or 'an unrecorded digest'}, "
            f"from its {image.seen_in}"
        ),
        entry=WorkloadEntry(
            service=service,
            repository=declared.repository,
            repo_url=repo.url if repo else None,
            image=image.reference,
            image_digest=image.digest,
            deployed_commit=commit or _commit_unknown(image),
            iac_repo=iac_repo,
            iac_repo_url=iac.url if iac else None,
            tenancy=declared.tenancy,
            source=MappingSource.IMAGE,
        ),
    )


def _from_pattern(config: Config, seed: Sequence[SeedEntry], service: str) -> Derivation:
    """The fallback: what config.yaml's ``serves`` patterns declare, and nothing more.

    The naming rule of 1.4 is deliberately not applied here. A pattern is a
    person's statement that this repository is deployed under these names —
    which is the whole point of the stopgap — while the seed is a document that
    person did not write.
    """
    declared = config.repo_serving(service, RepoKind.APPLICATION)
    if declared is None:
        return Derivation(
            service=service,
            outcome=MappingOutcome.NOT_MAPPED,
            reason=(
                f"no event in the window carries an image for {service} and no serves pattern "
                f"in config.yaml claims it, so nothing states which repository it runs"
            ),
        )
    repository = declared.url.rstrip("/").rsplit("/", 1)[-1]
    entry = seed_for(list(seed), repository)
    iac = config.repo_named(entry.iac_repo) if entry and entry.iac_repo else None
    return Derivation(
        service=service,
        outcome=MappingOutcome.MAPPED,
        reason=(
            f"no event in the window carries an image for {service}; config.yaml's serves "
            f"patterns declare it to be {declared.url}"
        ),
        entry=WorkloadEntry(
            service=service,
            repository=repository,
            repo_url=declared.url,
            deployed_commit=Unknown(
                reason=(
                    f"{service} emitted no image event in the window, so nothing says which "
                    f"build it runs — the mapping itself is a naming pattern, not an observation"
                )
            ),
            iac_repo=entry.iac_repo if entry else None,
            iac_repo_url=iac.url if iac else None,
            tenancy=entry.tenancy
            if entry
            else Unknown(
                reason=(
                    f"the seed names no repository {repository!r}, so whether it runs one "
                    f"deployment per customer is stated nowhere"
                )
            ),
            source=MappingSource.PATTERN,
        ),
    )


def derive_workload(
    config: Config,
    seed: Sequence[SeedEntry],
    service: str,
    events: Sequence[dict[str, Any]],
) -> Derivation:
    """Which repository this service runs: from its own images, else from a pattern."""
    image = latest_image(events)
    if image is None:
        return _from_pattern(config, seed, service)
    return _from_image(config, seed, service, image)
