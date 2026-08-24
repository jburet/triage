"""The direct Anthropic client, and how a run chooses between it and the proxy.

Offline: the SDK is replaced by a stub that records the request. What is worth
pinning is that both clients answer through the *same* mechanism — one forced
tool call, validated against the schema — because a local run that answered a
different way would not be evidence about the production one.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from triage.config import LLMProvider, Settings
from triage.integrations.github import GitHubError, GitHubRestClient
from triage.llm import AnthropicClient, LiteLLMClient, StructuredOutputError, tool_name
from triage.runtime import build_github, build_llm
from triage.schemas.collection import (
    AlertClassification,
    FollowUpPlan,
    Qualification,
)


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


def test_a_proxy_is_addressed_by_the_tier_unless_it_is_told_otherwise():
    """A proxy configured for Triage publishes the aliases; a shared one does not."""
    aliases = build_llm(a_settings(litellm_url="https://shared.example/v1"))
    assert isinstance(aliases, LiteLLMClient)
    assert aliases.model_for("analysis") == "analysis"

    named = build_llm(
        a_settings(
            litellm_url="https://shared.example/v1",
            model_triage="small",
            model_analysis="mid",
            model_diagnosis="big",
        )
    )
    assert isinstance(named, LiteLLMClient)
    assert named.model_for("analysis") == "mid"


def test_naming_only_some_tiers_for_a_proxy_is_refused():
    """Half-mapped fails on one tier at whatever hour that node first runs."""
    settings = a_settings(
        llm_provider=LLMProvider.LITELLM,
        litellm_url="https://shared.example/v1",
        model_triage="small",
    )

    with pytest.raises(ValueError, match="TRIAGE_MODEL_ANALYSIS, TRIAGE_MODEL_DIAGNOSIS"):
        build_llm(settings)


@pytest.mark.asyncio
async def test_an_unset_github_token_names_the_variable_rather_than_returning_401():
    """An unset token and a token that cannot see the repository are different problems.

    M6 2.12 turns a failed read into an Unknown per repository and carries on, so a
    401 from an empty Authorization header would reach the mapping report once per
    repository and read exactly like a permissions problem.
    """
    client = build_github(a_settings(github_token=""))

    with pytest.raises(GitHubError, match="TRIAGE_GITHUB_TOKEN is unset"):
        await client.commit_for_tag("https://github.com/zeenea/datacatalog", "501")


def test_a_configured_github_token_builds_the_real_client():
    assert isinstance(build_github(a_settings(github_token="ghp-test")), GitHubRestClient)


def a_proxy_client(reply: Reply) -> tuple[LiteLLMClient, StubMessages]:
    messages = StubMessages(reply)
    client = LiteLLMClient(
        "https://litellm.example.test/v1",
        "sk-proxy",
        models={"analysis": "an-alias"},
        client=StubAnthropic(messages),
    )
    return client, messages


async def test_the_proxy_is_asked_the_same_way_the_api_is():
    """One forced tool call, not an OpenAI-shaped `function_calling` round trip.

    Measured on 2026-08-24 against the real proxy: the OpenAI shape returned a
    parsable `Qualification` 4 times in 8, the Anthropic shape 6 — and the four
    losses carried `</summary>` and `<parameter name=…>` markup cut out of the
    model's answer by a partial parser, a failure the native shape cannot have
    (ADR-0022).
    """
    client, messages = a_proxy_client(Reply(content=[CLASSIFIED]))

    await client.call("analysis", "why did it restart?", AlertClassification)

    (request,) = messages.requests
    assert request["tool_choice"] == {"type": "tool", "name": tool_name(AlertClassification)}
    expected = AlertClassification.model_json_schema()
    assert request["tools"][0]["input_schema"]["properties"] == expected["properties"]
    assert "response_format" not in request


async def test_the_proxy_keeps_resolving_a_tier_it_was_given_no_name_for():
    """The tier *is* the model name on a proxy configured for Triage; only a
    shared proxy publishing its own names needs `TRIAGE_MODEL_*` (ADR-0007)."""
    client, messages = a_proxy_client(Reply(content=[CLASSIFIED]))

    await client.call("triage", "classify", AlertClassification)
    await client.call("analysis", "classify", AlertClassification)

    assert [request["model"] for request in messages.requests] == ["triage", "an-alias"]


def test_the_proxy_is_addressed_at_its_root_not_its_openai_path():
    """The SDK appends `/v1/messages`; leaving `/v1` on would ask for `/v1/v1/…`."""
    client = LiteLLMClient("https://litellm.example.test/v1", "sk-proxy")

    assert client.base_url == "https://litellm.example.test"


async def test_a_list_that_arrived_as_text_is_decoded_rather_than_refused():
    """The observed corruption: the value is the model's own JSON with the
    tool-call markup still trailing it. Decoding the leading value is parsing,
    not repair — nothing is invented and the schema still has the last word."""
    client, _ = a_proxy_client(
        Reply(
            content=[
                Block(
                    type="tool_use",
                    input={
                        "alert_class": '"crash_restart"</alert_class>\n',
                        "reason": "the container exited 137",
                    },
                )
            ]
        )
    )

    result = await client.call("analysis", "classify", AlertClassification)

    assert result.alert_class.value == "crash_restart"
    assert result.reason == "the container exited 137"


QUALIFIED = Block(
    type="tool_use",
    input={
        "summary": "The pod restarted once.",
        "causes": [
            {
                "cause_type": "infra",
                "service": "plt-hcl-software-uat",
                "description": "The liveness probe times out during startup.",
                "rank_score": 0.7,
            }
        ],
    },
)


async def test_a_schema_that_can_say_strict_says_it():
    """Measured: 6/6 valid with strict against 3/8 without, on the prompt that
    was losing one live incident in two (ADR-0022)."""
    client, messages = a_client(Reply(content=[QUALIFIED]))

    await client.call("analysis", "qualify this", Qualification)

    (tool,) = messages.requests[0]["tools"]
    assert tool["strict"] is True
    assert tool["input_schema"]["additionalProperties"] is False
    assert tool["input_schema"]["$defs"]["ProposedCause"]["additionalProperties"] is False


async def test_the_bounds_strict_will_not_take_are_still_enforced_after_it():
    """`rank_score`'s range and `causes`'s minimum length leave the wire and stay
    in the schema: the API enforces the structure, Pydantic the values."""
    client, messages = a_client(Reply(content=[QUALIFIED]))

    await client.call("analysis", "qualify this", Qualification)

    schema = messages.requests[0]["tools"][0]["input_schema"]
    assert "minItems" not in schema["properties"]["causes"]
    assert "maximum" not in schema["$defs"]["ProposedCause"]["properties"]["rank_score"]
    with pytest.raises(ValidationError):
        Qualification(summary="x", causes=[])


async def test_a_schema_with_an_optional_field_is_sent_as_it_is():
    """Strict requires every property; listing an optional one would make the
    model fill a field the schema says it may leave out."""
    client, messages = a_client(Reply(content=[Block(type="tool_use", input={"requests": []})]))

    await client.call("analysis", "anything else?", FollowUpPlan)

    (tool,) = messages.requests[0]["tools"]
    assert "strict" not in tool
    assert tool["input_schema"] == FollowUpPlan.model_json_schema()


class _RefusingStrict:
    """A proxy on a LiteLLM too old to know the field (observed 2026-08-24)."""

    def __init__(self, reply: Reply) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Reply:
        self.requests.append(kwargs)
        if "strict" in kwargs["tools"][0]:
            error = Exception("tools.0.custom.strict: Extra inputs are not permitted")
            error.status_code = 400  # type: ignore[attr-defined]
            raise error
        return self.reply


async def test_a_proxy_that_will_not_take_strict_is_asked_without_it():
    messages = _RefusingStrict(Reply(content=[QUALIFIED]))
    client = LiteLLMClient(
        "https://litellm.example.test/v1", "sk-proxy", client=StubAnthropic(messages)
    )

    first = await client.call("analysis", "qualify this", Qualification)
    await client.call("analysis", "qualify again", Qualification)

    assert first.causes[0].service == "plt-hcl-software-uat"
    sent_strict = ["strict" in request["tools"][0] for request in messages.requests]
    assert sent_strict == [True, False, False], "it should stop offering strict once refused"
