"""The pure rules of the mapping: who claims a repository, and what may be named after what."""

from tests.conftest import declaring
from triage.mapping.resolve import unclaimed
from triage.mapping.seed import load_seed


def test_a_seed_repository_no_team_declares_is_unclaimed():
    seed = load_seed()
    config = declaring("github.com/zeenea/platform")

    missing = unclaimed(config, seed)

    assert "platform" not in missing
    assert "studio" in missing
    assert len(missing) == len(seed) - 1


def test_nothing_is_invented_for_an_unclaimed_repository():
    """Its team stays unknown: config.yaml is where ownership is stated."""
    config = declaring("github.com/zeenea/platform")
    assert config.repo_named("studio") is None


def test_a_repository_is_claimed_by_the_url_it_is_declared_under():
    config = declaring("github.com/zeenea/platform")
    declared = config.repo_named("platform")
    assert declared is not None
    assert declared.url == "github.com/zeenea/platform"


def test_a_repository_whose_name_only_prefixes_a_declared_one_is_not_claimed():
    """`platform-infra` is a different repository from `platform`, and a suffix match
    that conflated them would point every platform analysis at the Terraform."""
    config = declaring("github.com/zeenea/platform-infra")
    assert config.repo_named("platform") is None
