"""The direct Anthropic client, and how a run chooses between it and the proxy.

Offline: the SDK is replaced by a stub that records the request. What is worth
pinning is that both clients answer through the *same* mechanism — one forced
tool call, validated against the schema — because a local run that answered a
different way would not be evidence about the production one.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from triage.config import LLMProvider, Settings
from triage.llm import AnthropicClient, LiteLLMClient, StructuredOutputError, tool_name
from triage.runtime import build_llm
from triage.schemas.collection import AlertClassification


@dataclass
class Block:
    type: str
    input: dict[str, Any] | None = None
    text: str | None = None


@dataclass
class Reply:
    content: list[Block]
    stop_reason: str = "tool_use"


@dataclass
class StubMessages:
    reply: Reply
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> Reply:
        self.requests.append(kwargs)
        return self.reply


@dataclass
class StubAnthropic:
    messages: StubMessages


def a_client(reply: Reply) -> tuple[AnthropicClient, StubMessages]:
    messages = StubMessages(reply)
    client = AnthropicClient(
        "sk-test",
        {"triage": "a-small-model", "analysis": "a-mid-model", "diagnosis": "a-big-model"},
        client=StubAnthropic(messages),
    )
    return client, messages


CLASSIFIED = Block(
    type="tool_use",
    input={"alert_class": "crash_restart", "reason": "The monitor counts container kills."},
)


async def test_a_tier_becomes_one_forced_tool_call_validated_against_the_schema():
    client, messages = a_client(Reply(content=[CLASSIFIED]))

    result = await client.call("triage", "classify this", AlertClassification)

    assert result.alert_class.value == "crash_restart"
    request = messages.requests[0]
    assert request["model"] == "a-small-model"
    assert request["tool_choice"] == {"type": "tool", "name": "AlertClassification"}
    assert request["tools"][0]["input_schema"]["properties"].keys() >= {"alert_class", "reason"}
    assert request["messages"] == [{"role": "user", "content": "classify this"}]


async def test_temperature_is_never_sent():
    """The current models reject it; only the OpenAI-shaped proxy client needs one."""
    client, messages = a_client(Reply(content=[CLASSIFIED]))

    await client.call("analysis", "anything", AlertClassification)

    assert "temperature" not in messages.requests[0]
    assert "effort" not in str(messages.requests[0])


async def test_an_answer_with_no_tool_call_names_the_tier_and_the_schema():
    client, _ = a_client(Reply(content=[Block(type="text", text="I would rather chat.")]))

    with pytest.raises(StructuredOutputError, match="AlertClassification"):
        await client.call("diagnosis", "anything", AlertClassification)


async def test_a_refusal_is_a_structured_output_failure_not_a_silent_none():
    client, _ = a_client(Reply(content=[], stop_reason="refusal"))

    with pytest.raises(StructuredOutputError):
        await client.call("triage", "anything", AlertClassification)


def test_a_tier_with_no_model_configured_names_the_variable():
    client, _ = a_client(Reply(content=[CLASSIFIED]))
    bare = AnthropicClient("sk-test", {}, client=object())

    assert client.model_for("analysis") == "a-mid-model"
    with pytest.raises(LookupError, match="TRIAGE_MODEL_ANALYSIS"):
        bare.model_for("analysis")


def test_tool_names_are_derived_from_the_schema_and_stay_acceptable():
    assert tool_name(AlertClassification) == "AlertClassification"


def a_settings(**overrides: object) -> Settings:
    """Built without reading a .env, so the choice is the test's and not the machine's."""
    base: dict[str, object] = {"_env_file": None, "anthropic_api_key": "", "model_triage": ""}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_auto_prefers_the_proxy_and_falls_back_to_the_key():
    assert isinstance(build_llm(a_settings()), LiteLLMClient)

    direct = build_llm(
        a_settings(
            anthropic_api_key="sk-ant",
            model_triage="a",
            model_analysis="b",
            model_diagnosis="c",
        )
    )
    assert isinstance(direct, AnthropicClient)


def test_a_configured_proxy_keeps_its_guardrails_even_with_a_key_present():
    """Auto must not quietly bypass the spend caps on a deployment that has a proxy."""
    settings = a_settings(
        anthropic_api_key="sk-ant",
        litellm_url="http://litellm.triage.svc:4000/v1",
        model_triage="a",
        model_analysis="b",
        model_diagnosis="c",
    )

    assert isinstance(build_llm(settings), LiteLLMClient)


def test_choosing_anthropic_without_the_models_says_which_are_missing():
    settings = a_settings(
        llm_provider=LLMProvider.ANTHROPIC, anthropic_api_key="sk-ant", model_triage="a"
    )

    with pytest.raises(ValueError, match="TRIAGE_MODEL_ANALYSIS, TRIAGE_MODEL_DIAGNOSIS"):
        build_llm(settings)


def test_no_key_is_left_to_the_sdk_rather_than_refused():
    """`ant auth login` writes a profile the SDK reads; refusing it helps nobody."""
    settings = a_settings(
        llm_provider=LLMProvider.ANTHROPIC,
        model_triage="a",
        model_analysis="b",
        model_diagnosis="c",
    )

    assert isinstance(build_llm(settings), AnthropicClient)
