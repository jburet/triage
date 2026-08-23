"""The model-access layer.

The interesting case is failure: a proxy misconfiguration returns a response with
no parsable structured output, and the value that then flows through the graph is
``None``. Caught at the call site it names the tier and the schema; three nodes
later it is an AttributeError on an unrelated line.
"""

import pytest

from tests.conftest import a_draft, a_verdict
from triage.llm import FakeLLM, LiteLLMClient, StructuredOutputError
from triage.schemas import ReviewVerdict, TicketDraft


class _NullRunnable:
    async def ainvoke(self, _prompt: str) -> None:
        return None


class _NullChat:
    def with_structured_output(self, *_args: object, **_kwargs: object) -> _NullRunnable:
        return _NullRunnable()


async def test_unparsable_structured_output_fails_at_the_call_site(monkeypatch):
    client = LiteLLMClient("http://proxy.invalid/v1", "key")
    monkeypatch.setattr(client, "_chat", lambda _tier: _NullChat())

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


def test_parallel_tool_calls_is_never_sent():
    """A Bedrock-backed proxy 400s on it, and one named tool has nothing to parallelise.

    LiteLLM cannot translate `parallel_tool_calls` for Bedrock, sweeps the
    unsupported parameters into `additionalModelRequestFields`, and Bedrock then
    rejects the whole request: "the additional field tool_choice/type conflicts
    with the existing field toolConfig.toolChoice.tool". Found on a live run.
    """
    chat = LiteLLMClient("http://proxy.invalid/v1", "key")._chat("analysis")

    assert "parallel_tool_calls" in chat.disabled_params
