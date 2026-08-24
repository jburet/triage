"""The 'never invent' guarantee, tested where it is enforced: the schemas."""

import pytest
from pydantic import ValidationError

from tests.conftest import some_findings
from triage.schemas import Confidence, Diagnosis, TicketDraft, TimeWindow, Unknown
from triage.schemas.analysis import ConfiguredValue
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


def a_configured_value(**overrides):
    base = {
        "setting": "readinessProbe.timeoutSeconds",
        "chart_default": "1, in helm/zeenea-platform/values.yaml",
        "tenant_value": Unknown(
            reason=(
                "plt-hcl-software-uat is one StatefulSet of a mono-tenant chart and its "
                "own overrides are not in this repository"
            )
        ),
    }
    base.update(overrides)
    return ConfiguredValue.model_validate(base)


def test_a_chart_default_and_this_tenants_value_are_different_facts():
    """M6 3.4: 40-odd parameters are overridden per tenant, so the chart's number is
    the chart's. Quoted as the tenant's it is a wrong answer that reads like a right one."""
    value = a_configured_value()

    assert isinstance(value.tenant_value, Unknown)
    assert value.chart_default == "1, in helm/zeenea-platform/values.yaml"


def test_stating_this_tenants_value_means_naming_where_it_was_read():
    with pytest.raises(ValidationError, match="read in"):
        a_configured_value(tenant_value="5, twice the chart default")


def test_a_tenant_value_that_was_actually_read_is_admitted():
    value = a_configured_value(
        tenant_value="5, twice the chart default",
        tenant_value_read_in="tenants/hcl-software-uat/values.yaml",
    )

    assert value.tenant_value == "5, twice the chart default"


def test_an_analysis_says_which_configured_values_its_answer_rests_on():
    findings = some_findings(configured_values=[a_configured_value()])

    assert isinstance(findings.configured_values[0].tenant_value, Unknown)


def test_an_analysis_that_rests_on_no_configured_value_has_to_say_so():
    """An empty list asserts nothing, and "the answer needs none" and "I did not look"
    are different facts about a chart nobody read."""
    with pytest.raises(ValidationError):
        some_findings(configured_values=[])
