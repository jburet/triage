"""The report a mapping pass writes about itself (M6 4.3, 4.4).

One text, two surfaces: the platform channel and `make run-mapping`. What is
worth pinning is the split — a mapping derived from the running image and one
guessed from a name pattern must not be counted together, because the whole
point of the milestone is that they are different facts.
"""

from tests.conftest import a_workload
from triage.mapping.report import render, summarise
from triage.schemas.system_map import Derivation, MappingOutcome


def derivation(service: str, outcome: MappingOutcome, **entry: object) -> Derivation:
    return Derivation(
        service=service,
        outcome=outcome,
        reason=f"{service}: {outcome.value}",
        entry=a_workload(service=service, **entry) if entry else None,
    )


def test_a_mapping_on_record_is_counted_by_what_answered_for_it():
    report = summarise(
        [
            derivation("plt-a", MappingOutcome.MAPPED, source="image"),
            derivation("plt-b", MappingOutcome.MAPPED, source="pattern", image=None),
        ]
    )

    assert [line.service for line in report.by_image] == ["plt-a"]
    assert [line.service for line in report.by_pattern] == ["plt-b"]


def test_a_mapping_this_pass_declined_to_rewrite_is_still_a_mapping():
    """`unchanged` is 2.5's answer, not a gap: the row on record already says it."""
    report = summarise([derivation("plt-a", MappingOutcome.UNCHANGED, source="image")])

    assert [line.service for line in report.by_image] == ["plt-a"]
    assert report.unmapped == []


def test_a_conflict_is_not_filed_as_a_gap():
    """1.4: a mapping to check by hand, which is a different thing to look at."""
    report = summarise([derivation("plt-a", MappingOutcome.CONFLICT)])

    assert [line.service for line in report.conflicting] == ["plt-a"]
    assert report.unmapped == []


def test_an_image_no_repository_is_named_after_is_a_gap_carrying_its_reason():
    report = summarise([derivation("plt-a", MappingOutcome.UNRESOLVED_IMAGE)])

    assert [(line.service, line.detail) for line in report.unmapped] == [
        ("plt-a", "plt-a: unresolved_image")
    ]


def test_a_mapped_workload_whose_chart_was_not_found_is_reported_as_well():
    report = summarise(
        [
            derivation(
                "plt-a",
                MappingOutcome.MAPPED,
                source="image",
                iac_repo="platform-infra",
                iac_repo_url="github.com/zeenea/platform-infra",
            )
        ]
    )

    assert [line.service for line in report.by_image] == ["plt-a"]
    assert [line.service for line in report.without_chart] == ["plt-a"]


def test_a_workload_whose_chart_was_found_is_not_reported_as_missing_one():
    report = summarise(
        [
            derivation(
                "plt-a",
                MappingOutcome.MAPPED,
                source="image",
                iac_repo_url="github.com/zeenea/platform-infra",
                iac_paths=["helm/zeenea-platform/values.yaml"],
            )
        ]
    )

    assert report.without_chart == []


def test_the_rendered_report_names_every_category_that_has_something_in_it():
    report = summarise(
        [
            derivation("plt-a", MappingOutcome.MAPPED, source="image"),
            derivation("plt-b", MappingOutcome.NOT_MAPPED),
        ],
        unclaimed=["studio"],
    )

    text = render(report)

    assert "2 services" in text
    assert "*Mapped from the running image* (1)" in text
    assert "*Not mapped* (1)" in text
    assert "Conflicting" not in text
    assert "studio" in text
