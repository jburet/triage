"""The rule that decides whether a merge can have changed a summary (ADR-0015)."""

from triage.analysis.invalidation import area_of, invalidation_for
from triage.config import RepoKind


def test_a_merge_touching_nothing_the_summariser_reads_leaves_the_summary_standing():
    decision = invalidation_for(
        ["CHANGELOG.md", "tests/test_payments.py", "docs/runbook.rst"], RepoKind.APPLICATION
    )

    assert not decision.stale
    assert decision.read_paths == ()


def test_a_merge_touching_a_file_the_summariser_reads_invalidates_the_summary():
    decision = invalidation_for(["CHANGELOG.md", "src/payments/api.py"], RepoKind.APPLICATION)

    assert decision.stale
    assert decision.read_paths == ("src/payments/api.py",)
    assert "src/payments/api.py" in decision.reason


def test_terraform_is_judged_by_the_terraform_selection_not_the_application_one():
    paths = ["modules/payments/main.tf"]

    assert invalidation_for(paths, RepoKind.TERRAFORM).stale
    assert not invalidation_for(paths, RepoKind.APPLICATION).stale


def test_a_change_under_an_excluded_directory_is_inert_even_when_the_name_matches():
    decision = invalidation_for(["node_modules/left-pad/package.json"], RepoKind.APPLICATION)

    assert not decision.stale


def test_terraform_state_never_invalidates_a_summary_because_it_is_never_read():
    decision = invalidation_for(["environments/prod/terraform.tfstate"], RepoKind.TERRAFORM)

    assert not decision.stale


def test_an_empty_diff_leaves_the_summary_standing_and_says_so():
    decision = invalidation_for([], RepoKind.APPLICATION)

    assert not decision.stale
    assert decision.areas == frozenset()
    assert "no files" in decision.reason


def test_touched_areas_are_recorded_for_the_operator_even_when_inert():
    decision = invalidation_for(["docs/a.md", "docs/b.md"], RepoKind.APPLICATION)

    assert decision.areas == frozenset({"docs"})


def test_an_area_reaches_past_a_container_directory_to_the_package_inside_it():
    assert area_of("src/payments/api.py", RepoKind.APPLICATION) == "src/payments"
    assert area_of("payments/api.py", RepoKind.APPLICATION) == "payments"
    assert area_of("pyproject.toml", RepoKind.APPLICATION) == "<root>"


def test_a_terraform_module_is_its_own_area():
    assert area_of("modules/payments/main.tf", RepoKind.TERRAFORM) == "modules/payments"
