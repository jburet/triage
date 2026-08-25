"""The shipped ``config.yaml``, checked for the things a deployment depends on.

Every other test reads ``tests/fixtures/config.yaml`` instead, so that declaring a
team or a repository cannot turn the suite red. That leaves the real file with no
cover at all, which is worse: it is edited by operators rather than by this repo,
and the failures it causes surface as an empty mapping report or a KeyError
several nodes in. What is asserted here is only what holds for *any* valid
deployment — never a particular repository, which is exactly the coupling the
split removed.
"""

from triage.config import DEFAULT_CONFIG_PATH, RepoKind, WriteTargets, load_config


def test_the_shipped_config_parses():
    assert load_config(DEFAULT_CONFIG_PATH) is not None


def test_the_shipped_config_names_somewhere_to_report_what_it_could_not_attribute():
    assert load_config(DEFAULT_CONFIG_PATH).platform_channel()


def test_every_declared_repository_belongs_to_a_declared_team():
    config = load_config(DEFAULT_CONFIG_PATH)

    undeclared = sorted(
        {
            repo.url: repo.team for repo in config.repos if not config.declares_team(repo.team)
        }.items()
    )

    assert not undeclared, f"repositories whose team config.yaml does not declare: {undeclared}"


def test_an_iac_analysis_has_a_terraform_repository_to_read():
    """Without one, every infrastructure hypothesis answers Unknown and says why.

    M6 Phase 3 reads the chart from the IaC repository the seed names; that
    repository still has to be declared here for its URL and owner to be known.
    """
    config = load_config(DEFAULT_CONFIG_PATH)

    assert [repo.url for repo in config.repos if repo.kind is RepoKind.TERRAFORM], (
        "config.yaml declares no terraform repository, so iac_analysis has nothing to read"
    )


def test_the_shipped_config_writes_only_to_slack():
    """The release's one operational claim, stated in the file an operator edits.

    ADR-0023's Jira gate is a default in code and a line of YAML here. The
    default alone is not enough: an operator reading config.yaml must be able to
    see which systems this deployment may write to without reading Python.
    """
    assert load_config(DEFAULT_CONFIG_PATH).writes is WriteTargets.SLACK
    assert "writes:" in DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
