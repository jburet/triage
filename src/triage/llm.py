"""Model access, by tier.

Graph code asks for a *tier* — ``triage``, ``analysis`` or ``diagnosis`` — never
for a model. The mapping from tier to an actual Anthropic model lives in the
LiteLLM proxy configuration, which is also where budget guardrails are enforced
(ADR-0007). No model name appears anywhere under ``src/``.
"""

from __future__ import annotations

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
