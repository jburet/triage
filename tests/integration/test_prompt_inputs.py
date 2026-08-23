"""What the model is actually shown.

These tests cannot judge prompt *quality* — that is what ``evals/`` is for. What
they can guarantee is that nothing the diagnosis knows is lost on the way to the
model, which is the failure mode a canned-response test would otherwise hide.
"""

import json

import pytest

from tests.conftest import all_fixture_names, build_deps, load_diagnosis, run_config
from triage.graphs.ticket_pipeline import graph
from triage.schemas import ReviewVerdict, TicketDraft


def _tagged_block(prompt: str, tag: str) -> dict:
    body = prompt.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]
    return json.loads(body)


@pytest.mark.parametrize("fixture_name", all_fixture_names())
async def test_composer_sees_the_whole_diagnosis(config, fixture_name):
    diagnosis = load_diagnosis(fixture_name)
    deps = build_deps(config)
    # Force the composer to run even for a below-threshold fixture.
    deps.config.thresholds.ticket_confidence[diagnosis.feature] = diagnosis.confidence

    await graph.ainvoke({"diagnosis": diagnosis}, config=run_config(deps))

    prompt = deps.llm.calls_for(TicketDraft)[0].prompt
    assert _tagged_block(prompt, "diagnosis") == diagnosis.model_dump(mode="json")


@pytest.mark.parametrize("fixture_name", all_fixture_names())
async def test_unknowns_reach_the_model_as_explicit_markers(config, fixture_name):
    """An unknown must arrive as a marked absence, not as a blank the model can fill in."""
    diagnosis = load_diagnosis(fixture_name)
    deps = build_deps(config)
    deps.config.thresholds.ticket_confidence[diagnosis.feature] = diagnosis.confidence

    await graph.ainvoke({"diagnosis": diagnosis}, config=run_config(deps))
    payload = _tagged_block(deps.llm.calls_for(TicketDraft)[0].prompt, "diagnosis")

    for field in ("probable_cause",):
        value = payload[field]
        if isinstance(value, dict):
            assert value["unknown"] is True
            assert value["reason"]

    for item in payload["unknowns"]:
        assert item["question"]
        assert item["why_unresolved"]


async def test_reviewer_sees_the_diagnosis_not_only_the_draft(config, oom_diagnosis):
    """The failure only the reviewer can catch is a claim the diagnosis never made."""
    deps = build_deps(config)
    await graph.ainvoke({"diagnosis": oom_diagnosis}, config=run_config(deps))

    prompt = deps.llm.calls_for(ReviewVerdict)[0].prompt
    assert _tagged_block(prompt, "diagnosis") == oom_diagnosis.model_dump(mode="json")
    assert _tagged_block(prompt, "draft")


async def test_prompt_instructions_precede_the_data(config, oom_diagnosis):
    """Inputs are model-written prose; they must land inside a delimited block."""
    deps = build_deps(config)
    await graph.ainvoke({"diagnosis": oom_diagnosis}, config=run_config(deps))

    prompt = deps.llm.calls_for(TicketDraft)[0].prompt
    assert prompt.index("Never invent") < prompt.index("<diagnosis>")
