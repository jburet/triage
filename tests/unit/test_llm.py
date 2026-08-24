"""The model-access layer.

The interesting case is failure: a proxy misconfiguration returns a response with
no parsable structured output, and the value that then flows through the graph is
``None``. Caught at the call site it names the tier and the schema; three nodes
later it is an AttributeError on an unrelated line.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tests.conftest import a_draft, a_verdict
from triage.llm import AnthropicClient, FakeLLM, LiteLLMClient, StructuredOutputError
from triage.runtime import verify_models
from triage.schemas import ReviewVerdict, TicketDraft


@dataclass
class _Prose:
    """A reply that answered in text instead of calling the tool it was given."""

    content: list[SimpleNamespace] = field(
        default_factory=lambda: [SimpleNamespace(type="text", text="I think it restarted.")]
    )
    stop_reason: str = "end_turn"


@dataclass
class _AnsweringInProse:
    messages: object = field(default_factory=lambda: _Prose())

    async def create(self, **_kwargs: object) -> _Prose:
        return _Prose()


async def test_unparsable_structured_output_fails_at_the_call_site():
    client = LiteLLMClient(
        "http://proxy.invalid/v1",
        "key",
        client=SimpleNamespace(messages=_AnsweringInProse()),
    )

    with pytest.raises(StructuredOutputError, match="TicketDraft"):
        await client.call("analysis", "prompt", TicketDraft)


async def test_fake_repeats_its_last_response():
    """So a test that does not care about call counts supplies one element."""
    llm = FakeLLM(responses={TicketDraft: [a_draft()]})
    first = await llm.call("analysis", "a", TicketDraft)
    second = await llm.call("analysis", "b", TicketDraft)
    assert first == second
    assert len(llm.calls_for(TicketDraft)) == 2


async def test_fake_walks_a_sequence_in_order():
    llm = FakeLLM(responses={ReviewVerdict: [a_verdict(False, "no"), a_verdict(True)]})
    assert not (await llm.call("diagnosis", "a", ReviewVerdict)).passes
    assert (await llm.call("diagnosis", "b", ReviewVerdict)).passes


async def test_fake_refuses_a_schema_it_was_not_given():
    llm = FakeLLM(responses={})
    with pytest.raises(AssertionError, match="TicketDraft"):
        await llm.call("analysis", "a", TicketDraft)


def test_the_proxy_and_the_api_are_one_implementation():
    """Not a style point: it is what makes a local run evidence about production.

    While the proxy was addressed in the OpenAI shape the two paths differed in
    how a tool call was encoded, and only one of them corrupted it — which is
    exactly the kind of difference a local reproduction could never show
    (ADR-0022).
    """
    assert issubclass(LiteLLMClient, AnthropicClient)
    assert LiteLLMClient.call is AnthropicClient.call


def a_proxy_publishing(*names: str, models: dict[str, str] | None = None) -> LiteLLMClient:
    client = LiteLLMClient(
        "http://proxy.invalid/v1",
        "key",
        models=models or {"triage": "haiku", "analysis": "sonnet", "diagnosis": "opus"},  # type: ignore[arg-type]
    )

    async def published() -> list[str] | None:
        return list(names) or None

    client.published_models = published  # type: ignore[method-assign]
    return client


async def test_a_model_name_the_proxy_does_not_serve_is_refused_before_anything_runs():
    """Both ways it fails live are worse than a refusal, and one of them is silent.

    On 2026-08-24 the diagnosis tier's name killed the run ninety seconds in, and
    the triage tier's failed quietly: the alert went unclassified, the generic
    sweep was collected instead of the crash recipe, and nothing said why.
    """
    client = a_proxy_publishing("sonnet", "opus")

    with pytest.raises(ValueError, match="TRIAGE_MODEL_TRIAGE") as refusal:
        await verify_models(client)

    assert "haiku" in str(refusal.value), "it must name what was asked for"
    assert "sonnet" in str(refusal.value), "and what the proxy does serve"


async def test_every_name_the_proxy_serves_is_no_objection():
    await verify_models(a_proxy_publishing("haiku", "sonnet", "opus"))


async def test_a_proxy_that_will_not_say_is_not_evidence_of_a_wrong_name():
    """Unreachable is not wrong. A blip at 03:00 must not be what stops the run."""
    await verify_models(a_proxy_publishing())


async def test_the_tier_aliases_are_the_proxys_own_and_are_not_checked():
    """With `TRIAGE_MODEL_*` unset the tier *is* the name, and a proxy configured
    for Triage publishes it under a listing we have no claim on."""
    await verify_models(LiteLLMClient("http://proxy.invalid/v1", "key"))


async def test_the_direct_client_has_no_proxy_to_ask():
    await verify_models(AnthropicClient("key", {"triage": "claude-x"}))  # type: ignore[dict-item]
