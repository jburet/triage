"""The two pure rules that decide whether a human gets interrupted."""

import pytest

from triage.config import Thresholds
from triage.nodes.confidence import passes_gate
from triage.nodes.dedup import should_realert
from triage.schemas import Confidence, Feature, TicketSection

# config.yaml ships F1 >= medium, F3 >= high (ADR-0002).
GATE_TRUTH_TABLE = [
    (Feature.F1, Confidence.LOW, False),
    (Feature.F1, Confidence.MEDIUM, True),
    (Feature.F1, Confidence.HIGH, True),
    (Feature.F3, Confidence.LOW, False),
    (Feature.F3, Confidence.MEDIUM, False),
    (Feature.F3, Confidence.HIGH, True),
]


@pytest.mark.parametrize(("feature", "confidence", "expected"), GATE_TRUTH_TABLE)
def test_confidence_gate_truth_table(config, feature, confidence, expected):
    assert passes_gate(confidence, feature, config) is expected


def test_f3_is_stricter_than_f1(config):
    """The asymmetry is the point: F3 is proactive and noisier, so it earns a higher bar."""
    assert (
        config.confidence_threshold(Feature.F3).rank > config.confidence_threshold(Feature.F1).rank
    )


# Defaults: alert at the 3rd occurrence, then every 5th (ADR-0003).
@pytest.mark.parametrize(
    ("occurrence", "expected"),
    [(1, False), (2, False), (3, True), (4, False), (7, False), (8, True), (12, False), (13, True)],
)
def test_recurrence_escalation_schedule(config, occurrence, expected):
    assert should_realert(occurrence, config.thresholds) is expected


def test_recurrence_schedule_respects_overrides():
    thresholds = Thresholds(
        ticket_confidence={Feature.F1: Confidence.MEDIUM, Feature.F3: Confidence.HIGH},
        dedup_recurrence_alert=2,
        dedup_recurrence_interval=2,
    )
    fired = [n for n in range(1, 11) if should_realert(n, thresholds)]
    assert fired == [2, 4, 6, 8, 10]


def test_ticket_renders_all_nine_sections():
    from tests.conftest import a_draft

    markdown = a_draft().to_markdown()
    for section in TicketSection:
        assert f"## {section.heading}" in markdown
    assert len(list(TicketSection)) == 9
