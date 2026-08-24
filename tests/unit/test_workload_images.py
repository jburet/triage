"""Reading the running image out of Datadog's events, against the captured incident.

Every string here came off the wire on 2026-08-22: the ECR registry, the digest,
the build number in `image_tag`, and the `alpine/openssl` init container that
shares the StatefulSet event with the workload's own image.
"""

import pytest

from tests.conftest import captured
from triage.mapping.images import latest_image, observed_images, split_reference

DIGEST = "sha256:2e15f697553acdbdd13ec687080f1b600d531b504b73603dede0bda606d1d87b"
REGISTRY = "097607883991.dkr.ecr.us-east-1.amazonaws.com/platform"


@pytest.fixture(scope="module")
def events():
    return captured("events_service")["data"]


@pytest.fixture(scope="module")
def statefulset_event(events):
    return next(
        event
        for event in events
        if (event["attributes"]["attributes"].get("changed_resource") or {}).get("type")
        == "kube_stateful_set"
    )


def test_the_repository_is_the_image_name_the_workload_reports(events):
    image = latest_image(events)
    assert image is not None
    assert image.repository == "platform"


def test_the_digest_and_the_tag_are_recorded_as_seen(events):
    image = latest_image(events)
    assert image.digest == DIGEST
    assert image.tag == "501"


def test_a_container_event_carries_the_image_in_its_tags(events):
    container = next(
        event
        for event in events
        if "started" in (event["attributes"].get("message") or "")
        and "changed_resource" not in event["attributes"]["attributes"]
    )
    (image,) = observed_images([container])
    assert image.repository == "platform"
    assert image.digest == DIGEST
    assert image.seen_in == "container event"


def test_a_statefulset_change_event_carries_a_full_reference(statefulset_event):
    (image,) = observed_images([statefulset_event])
    assert image.reference == f"{REGISTRY}:501@{DIGEST}"
    assert image.seen_in == "kube_stateful_set change event"


def test_an_init_container_is_not_read_as_the_workloads_image(statefulset_event):
    """`alpine/openssl` generates a keystore in that same event and is not what runs."""
    images = observed_images([statefulset_event])
    assert [image.repository for image in images] == ["platform"]


def test_an_event_with_no_image_contributes_nothing(events):
    alert = next(
        event
        for event in events
        if "New error logs" in (event["attributes"]["attributes"].get("title") or "")
    )
    assert observed_images([alert]) == []


def test_images_come_back_oldest_first(events):
    stamps = [image.at for image in observed_images(events)]
    assert stamps == sorted(stamps)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (f"{REGISTRY}:501@{DIGEST}", ("platform", "501", DIGEST)),
        (f"{REGISTRY}@{DIGEST}", ("platform", None, DIGEST)),
        (REGISTRY, ("platform", None, None)),
        ("alpine/openssl", ("openssl", None, None)),
        ("registry.internal:5000/studio:1.2.3", ("studio", "1.2.3", None)),
    ],
)
def test_a_reference_splits_into_repository_tag_and_digest(reference, expected):
    assert split_reference(reference) == expected
