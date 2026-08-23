"""Model access, by tier.

Graph code asks for a *tier* — ``triage``, ``analysis`` or ``diagnosis`` — never
for a model. No model name appears anywhere under ``src/`` (ADR-0007).

There are two implementations of the one method, and they are interchangeable by
construction:

- :class:`LiteLLMClient` goes through the proxy, which resolves the tier aliases
  and enforces the per-run and per-day spend caps. This is how production runs.
- :class:`AnthropicClient` calls the API directly with an API key, for local runs
  and one-shots where standing up a proxy to try one alert is the reason the alert
  never gets tried. It resolves the tier from ``TRIAGE_MODEL_*`` environment
  variables — configuration, not code — so the "no model name under src/" rule
  holds, and so does the reason for it: which model serves a tier is not a code
  change.

What the direct client does *not* have is the guardrails. That is the honest cost
of the shortcut, and it is why the proxy stays the production path.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel

Tier: TypeAlias = Literal["triage", "analysis", "diagnosis"]

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """The model returned nothing that parsed as the requested schema."""

    def __init__(self, tier: Tier, schema: type[BaseModel]) -> None:
        super().__init__(
            f"tier {tier!r} returned no parsable {schema.__name__}; "
            f"check the LiteLLM alias and that the model supports tool use"
        )
        self.tier = tier
        self.schema = schema


class StructuredLLM(Protocol):
    """Every LLM call in Triage goes through this one method.

    Structured output only: a node that needs prose asks for a model with a
    prose field, so that the result is always validated against a schema.
    """

    async def call(self, tier: Tier, prompt: str, schema: type[T]) -> T: ...


class LiteLLMClient:
    """Real client. LiteLLM speaks the OpenAI protocol, so `model` is the alias."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 120.0) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._cache: dict[Tier, Any] = {}

    def _chat(self, tier: Tier) -> Any:
        if tier not in self._cache:
            from langchain_openai import ChatOpenAI

            self._cache[tier] = ChatOpenAI(
                model=tier,
                base_url=self._base_url,
                api_key=self._api_key,
                timeout=self._timeout,
                temperature=0,
            )
        return self._cache[tier]

    async def call(self, tier: Tier, prompt: str, schema: type[T]) -> T:
        # `function_calling`, not the library default of `json_schema`: behind this
        # proxy every model is an Anthropic one, where tool use is native and
        # OpenAI-style `response_format` relies on LiteLLM translating it.
        runnable = self._chat(tier).with_structured_output(
            schema, method="function_calling", include_raw=False
        )
        result = await runnable.ainvoke(prompt)
        if result is None:
            # LangChain returns None when the response carries no parsable
            # structured output. Failing here names the tier and the schema;
            # letting it through surfaces as an AttributeError several nodes later.
            raise StructuredOutputError(tier, schema)
        return cast(T, result)


@dataclass(frozen=True)
class RecordedCall:
    tier: Tier
    prompt: str
    schema: type[BaseModel]


Responder: TypeAlias = "Sequence[BaseModel] | Callable[[str], BaseModel]"


@dataclass
class FakeLLM:
    """Deterministic stand-in, keyed by the schema the node asks for.

    A sequence is consumed one call at a time and its last element repeats, so a
    test that wants "fail twice then pass" writes exactly that and a test that
    does not care about call counts writes a single element.
    """

    responses: Mapping[type[BaseModel], Responder]
    calls: list[RecordedCall] = field(default_factory=list)
    _cursor: dict[type[BaseModel], int] = field(default_factory=dict)

    async def call(self, tier: Tier, prompt: str, schema: type[T]) -> T:
        self.calls.append(RecordedCall(tier=tier, prompt=prompt, schema=schema))
        try:
            responder = self.responses[schema]
        except KeyError as exc:
            raise AssertionError(
                f"FakeLLM has no response configured for {schema.__name__}"
            ) from exc

        if callable(responder):
            return cast(T, responder(prompt))

        index = min(self._cursor.get(schema, 0), len(responder) - 1)
        self._cursor[schema] = index + 1
        return cast(T, responder[index])

    def calls_for(self, schema: type[BaseModel]) -> list[RecordedCall]:
        return [call for call in self.calls if call.schema is schema]


MAX_TOKENS = 16_000
"""Enough for the largest schema here; low enough to stay under the HTTP timeout."""

DEFAULT_TIMEOUT = 300.0


def tool_name(schema: type[BaseModel]) -> str:
    """A tool name Anthropic accepts, derived from the schema being asked for."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__)[:64]


class AnthropicClient:
    """Direct Anthropic access, for local development without the LiteLLM proxy.

    Structured output is tool use, as it is through the proxy: one tool whose input
    schema *is* the Pydantic schema, and ``tool_choice`` forcing it. Keeping both
    clients on the same mechanism is the point — a local run that answered through
    a different path would not be evidence about the production one.

    Two things are deliberately not sent. ``temperature`` is rejected outright by
    the current models, and the proxy path only sets it because the OpenAI-shaped
    client insists on one. And ``effort`` is not sent either: it is a per-model
    capability, the operator chooses the models here, and a request carrying it to
    a model that does not take it fails the run rather than costing a little more.
    """

    def __init__(
        self,
        api_key: str | None,
        models: Mapping[Tier, str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = MAX_TOKENS,
        client: Any = None,
    ) -> None:
        self._api_key = api_key
        self._models = dict(models)
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._client = client

    def _anthropic(self) -> Any:
        """The SDK client. With no key, the SDK resolves its own credentials.

        An unset key is not an error here: the SDK reads ``ANTHROPIC_API_KEY`` and
        the profile written by ``ant auth login``, and refusing those would make
        the local path harder to use than the proxy it exists to avoid.
        """
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = (
                AsyncAnthropic(api_key=self._api_key, timeout=self._timeout)
                if self._api_key
                else AsyncAnthropic(timeout=self._timeout)
            )
        return self._client

    def model_for(self, tier: Tier) -> str:
        try:
            return self._models[tier]
        except KeyError as exc:
            raise LookupError(
                f"no model configured for tier {tier!r}: set TRIAGE_MODEL_{tier.upper()}"
            ) from exc

    async def call(self, tier: Tier, prompt: str, schema: type[T]) -> T:
        name = tool_name(schema)
        message = await self._anthropic().messages.create(
            model=self.model_for(tier),
            max_tokens=self._max_tokens,
            tools=[
                {
                    "name": name,
                    "description": (schema.__doc__ or f"Return a {schema.__name__}.").strip(),
                    "input_schema": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": name},
            messages=[{"role": "user", "content": prompt}],
        )
        if getattr(message, "stop_reason", None) == "refusal":
            raise StructuredOutputError(tier, schema)
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                return schema.model_validate(block.input)
        raise StructuredOutputError(tier, schema)
