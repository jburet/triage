"""The 'never invent' guarantee, tested where it is enforced: the schemas."""

import pytest
from pydantic import ValidationError

from triage.schemas import Confidence, Diagnosis, TicketDraft, TimeWindow, Unknown
from triage.schemas.common import reject_placeholder

PLACEHOLDERS = ["", "  ", "N/A", "n/a", "TBD", "todo", "None", "-", "?", "unknown", "Unknown."]


@pytest.mark.parametrize("value", PLACEHOLDERS)
def test_placeholders_are_rejected(value):
    """A field that says nothing must not be able to masquerade as a filled one."""
    with pytest.raises(ValueError, match=r"placeholder|too short"):
        reject_placeholder(value)


def test_real_prose_is_accepted_and_stripped():
    assert reject_placeholder("  p95 rose to 1.4 s  ") == "p95 rose to 1.4 s"


def test_unknown_requires_a_reason():
    """'We do not know' is only acceptable with why."""
    with pytest.raises(ValidationError):
        Unknown(reason="")
    assert Unknown(reason="No heap dump was captured.").reason


def test_unknown_that_states_why_is_not_a_placeholder():
    """The distinction the design rests on: bare 'unknown' is banned, 'unknown because X' is not."""
    assert reject_placeholder("Unknown: node metrics had already aged out of retention.")


def test_confidence_ordering():
    assert Confidence.HIGH.at_least(Confidence.MEDIUM)
    assert Confidence.MEDIUM.at_least(Confidence.MEDIUM)
    assert not Confidence.LOW.at_least(Confidence.MEDIUM)


def test_time_window_must_be_ordered():
    with pytest.raises(ValidationError, match="ends before it starts"):
        TimeWindow(start="2026-08-22T10:00:00Z", end="2026-08-22T09:00:00Z")


def test_unknown_cause_cannot_carry_high_confidence(oom_diagnosis):
    """Confidence must be earned. Naming no cause is the clearest case of not earning it."""
    payload = oom_diagnosis.model_dump(mode="json")
    payload["probable_cause"] = {"unknown": True, "reason": "Nothing in telemetry distinguishes."}
    with pytest.raises(ValidationError, match="cannot exceed 'low'"):
        Diagnosis.model_validate(payload)


def test_high_confidence_needs_corroboration(oom_diagnosis):
    payload = oom_diagnosis.model_dump(mode="json")
    payload["evidence"] = payload["evidence"][:1]
    with pytest.raises(ValidationError, match="at least two independent"):
        Diagnosis.model_validate(payload)


def test_diagnosis_needs_some_evidence(oom_diagnosis):
    payload = oom_diagnosis.model_dump(mode="json")
    payload["evidence"] = []
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


@pytest.mark.parametrize("section", list(TicketDraft.model_fields))
def test_every_ticket_section_is_mandatory(section):
    """All nine specification sections, plus the summary. None may be omitted."""
    from tests.conftest import a_draft

    payload = a_draft().model_dump()
    del payload[section]
    with pytest.raises(ValidationError):
        TicketDraft.model_validate(payload)


@pytest.mark.parametrize("section", list(TicketDraft.model_fields))
def test_no_ticket_section_may_be_filler(section):
    from tests.conftest import a_draft

    payload = a_draft().model_dump()
    payload[section] = "N/A"
    with pytest.raises(ValidationError):
        TicketDraft.model_validate(payload)
