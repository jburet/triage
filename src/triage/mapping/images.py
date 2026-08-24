"""The image a workload is actually running, read out of Datadog's own events.

Every application image is built into the infra account's ECR under the
repository's own name, so the image is the one place a running service states
which repository it is — no pattern, no naming convention maintained by hand.

Two event shapes carry it, and both are in the captured incident: a containerd
container or task event, where it is in the tags
(``short_image:platform``, ``image_tag:501``,
``image_id:…/platform_sha256:2e15f697…``), and a change-tracking StatefulSet
event, where the workload's containers carry a full reference
(``…/platform:501@sha256:2e15f697…``). Init containers are deliberately not read:
``alpine/openssl`` is real, is in that same event, and is not what this service
runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

COMMIT_TAG = re.compile(r"^(?:sha-|sha_|git-|commit-)?([0-9a-f]{7,40})$", re.I)
"""A tag that is a commit. Requiring a letter as well is what keeps a build number out."""


@dataclass(frozen=True)
class ObservedImage:
    """One image seen running, with where in the event it was seen."""

    reference: str
    repository: str
    tag: str | None
    digest: str | None
    at: datetime | None
    seen_in: str


def split_reference(reference: str) -> tuple[str, str | None, str | None]:
    """``registry/name:tag@sha256:…`` into repository name, tag and digest."""
    digest_match = DIGEST.search(reference)
    digest = digest_match.group(0) if digest_match else None
    path = reference.split("@", 1)[0]
    tag: str | None = None
    head, _, candidate = path.rpartition(":")
    if head and "/" not in candidate:
        path, tag = head, candidate
    return path.rstrip("/").rsplit("/", 1)[-1], tag, digest


def commit_in_tag(tag: str | None) -> str | None:
    """The commit an image tag carries, when it carries one.

    ``501`` is what the captured tenant was running: a build number, seven of
    which would be indistinguishable from a short SHA. So a tag qualifies only if
    it is hexadecimal *and* contains a letter — a decimal build number is never
    read as a commit, at the price of missing the rare all-digit one.
    """
    match = COMMIT_TAG.match(tag or "")
    if match is None:
        return None
    commit = match.group(1).lower()
    return commit if any(character in "abcdef" for character in commit) else None


def _tags(event: dict[str, Any]) -> dict[str, str]:
    attributes = event.get("attributes") or {}
    split = (str(tag).partition(":") for tag in attributes.get("tags") or [])
    return {name: value for name, separator, value in split if separator}


def _at(event: dict[str, Any]) -> datetime | None:
    stamp = (event.get("attributes") or {}).get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:  # pragma: no cover - Datadog has always sent ISO-8601
        return None


def _from_tags(event: dict[str, Any]) -> ObservedImage | None:
    tags = _tags(event)
    name = tags.get("short_image")
    if not name:
        return None
    digest_match = DIGEST.search(tags.get("image_id", ""))
    tag = tags.get("image_tag")
    registry = tags.get("image_name", name)
    digest = digest_match.group(0) if digest_match else None
    return ObservedImage(
        reference=registry + (f":{tag}" if tag else "") + (f"@{digest}" if digest else ""),
        repository=name,
        tag=tag,
        digest=digest,
        at=_at(event),
        seen_in="container event",
    )


def _from_change(event: dict[str, Any]) -> ObservedImage | None:
    inner = (event.get("attributes") or {}).get("attributes") or {}
    containers = (inner.get("new_value") or {}).get("containers") or []
    reference = next(
        (str(container["image"]) for container in containers if container.get("image")), None
    )
    if reference is None:
        return None
    repository, tag, digest = split_reference(reference)
    return ObservedImage(
        reference=reference,
        repository=repository,
        tag=tag,
        digest=digest,
        at=_at(event),
        seen_in=f"{(inner.get('changed_resource') or {}).get('type', 'workload')} change event",
    )


def observed_images(events: Iterable[dict[str, Any]]) -> list[ObservedImage]:
    """Every image these events carry, oldest first."""
    found = [
        image
        for event in events
        for image in (_from_change(event) or _from_tags(event),)
        if image is not None
    ]
    return sorted(found, key=lambda image: (image.at is not None, image.at or datetime.min))


def latest_image(events: Sequence[dict[str, Any]]) -> ObservedImage | None:
    """The most recently observed image, which is the one running now."""
    images = observed_images(events)
    return images[-1] if images else None
