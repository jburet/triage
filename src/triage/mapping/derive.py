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

from triage.config import Config
from triage.mapping.images import ObservedImage, latest_image
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
            deployed_commit=Unknown(
                reason=f"the image {image.reference} was found; its commit has not been resolved"
            ),
            iac_repo=iac_repo,
            iac_repo_url=iac.url if iac else None,
            tenancy=declared.tenancy,
            source=MappingSource.IMAGE,
        ),
    )


def derive_workload(
    config: Config,
    seed: Sequence[SeedEntry],
    service: str,
    events: Sequence[dict[str, Any]],
) -> Derivation:
    """Which repository this service runs, from the images its own events carry."""
    image = latest_image(events)
    if image is None:
        return Derivation(
            service=service,
            outcome=MappingOutcome.NOT_MAPPED,
            reason=(
                f"no event in the window carries an image for {service}, so nothing states "
                f"which repository it runs"
            ),
        )
    return _from_image(config, seed, service, image)
