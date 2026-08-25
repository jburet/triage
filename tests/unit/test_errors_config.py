"""The ``errors:`` block and the environment filter it produces (M8 1.2, 1.3)."""

import pytest

from triage.config import DEFAULT_CONFIG_PATH, Config, load_config
from triage.errors.issues import environment_filter
from triage.schemas.common import Confidence, Feature
from triage.schemas.errors import ErrorPersona, ErrorTrack


def test_the_shipped_config_declares_the_errors_block():
    errors = load_config(DEFAULT_CONFIG_PATH).errors

    assert errors.tracks == [ErrorTrack.TRACE, ErrorTrack.LOGS]
    assert errors.persona is ErrorPersona.BACKEND
    assert errors.lookback_minutes == 60


def test_f2_earns_its_own_confidence_threshold():
    """A feature with no threshold raises several nodes in; assert it here instead."""
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.confidence_threshold(Feature.F2) is not None


def test_the_shipped_config_watches_the_environment_the_capture_came_from():
    assert environment_filter(load_config(DEFAULT_CONFIG_PATH)) == "env:prod"


def test_several_watched_environments_are_one_filter(config: Config):
    config.teams[0].environments = ["preprod", "prod"]

    assert environment_filter(config) == "env:(preprod OR prod)"


def test_watching_nothing_is_not_watching_everything(config: Config):
    """A tick with no environment configured must make no call, not an unfiltered one."""
    for team in config.teams:
        team.environments = []

    assert environment_filter(config) is None


@pytest.mark.parametrize("feature", list(Feature))
def test_every_feature_has_a_confidence_threshold(feature: Feature):
    thresholds = load_config(DEFAULT_CONFIG_PATH).thresholds.ticket_confidence

    assert isinstance(thresholds[feature], Confidence)
