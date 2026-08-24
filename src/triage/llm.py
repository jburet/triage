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

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel

Tier: TypeAlias = Literal["triage", "analysis", "diagnosis"]

T = TypeVar("T", bound=BaseModel)

MAX_TOKENS = 16_000
"""Enough for the largest schema here; low enough to stay under the HTTP timeout.

Both clients set it. Left to the provider's default, a summary of a fifty-module
repository came back truncated mid-field and failed validation twice — as
"database_access: field required", which reads like a model that ignored the
schema and is actually a model that ran out of room.
"""


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


DEFAULT_TIMEOUT = 300.0


def tool_name(schema: type[BaseModel]) -> str:
    """A tool name Anthropic accepts, derived from the schema being asked for."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__)[:64]


def _decoded(arguments: Any, schema: type[BaseModel]) -> Any:
    """Structured fields that arrived as text, decoded back to what they are.

    A tool call is supposed to carry ``input`` as an object, and against the
    Zeenea proxy on 2026-08-24 it sometimes did not: a field the schema declares
    as a list arrived as a string holding the model's own JSON with the
    tool-call markup still trailing it — ``[{…}]</causes>``. The leading value is
    decoded and the trailing markup dropped; a field the schema already declares
    as a string is left alone, so nothing that legitimately *is* prose is
    reinterpreted.

    Parsing, not repair: nothing is invented, the decode has to succeed on its
    own, and the schema still refuses whatever comes out (ADR-0022).
    """
    if not isinstance(arguments, dict):
        return arguments
    decoder = json.JSONDecoder()
    fields = schema.model_fields
    decoded = dict(arguments)
    for name, value in arguments.items():
        field_info = fields.get(name)
        if field_info is None or not isinstance(value, str) or field_info.annotation is str:
            continue
        try:
            parsed, _ = decoder.raw_decode(value.strip())
        except ValueError:
            continue
        decoded[name] = parsed
    return decoded


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
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = MAX_TOKENS,
        client: Any = None,
    ) -> None:
        self._api_key = api_key
        self._models = dict(models)
        self.base_url = base_url
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

            options: dict[str, Any] = {"timeout": self._timeout}
            if self._api_key:
                options["api_key"] = self._api_key
            if self.base_url:
                options["base_url"] = self.base_url
            self._client = AsyncAnthropic(**options)
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
                return schema.model_validate(_decoded(block.input, schema))
        raise StructuredOutputError(tier, schema)


class LiteLLMClient(AnthropicClient):
    """The proxy. The same request the API takes, sent to the proxy's own URL.

    LiteLLM serves the Anthropic protocol on the same host it serves the OpenAI
    one, so addressing it this way costs nothing in guardrails — the aliases
    resolve and the spend caps apply exactly as before — and buys back native
    tool use. That matters because the OpenAI shape is a translation: the proxy
    has to rebuild ``function.arguments`` as a JSON string from what the model
    returned, and it does not always rebuild it correctly (ADR-0022).

    The tier *is* the model name by default, which is what a proxy configured
    for Triage publishes. A shared proxy nobody will re-configure for us
    publishes its own names instead, so ``models`` maps tier to whatever that
    proxy calls it — from ``TRIAGE_MODEL_*``, the same variables the direct
    client reads. Graph code still asks for a tier and no model name appears
    under ``src/`` (ADR-0007).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        models: Mapping[Tier, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = MAX_TOKENS,
        client: Any = None,
    ) -> None:
        super().__init__(
            api_key,
            models or {},
            base_url=_without_openai_path(base_url),
            timeout=timeout,
            max_tokens=max_tokens,
            client=client,
        )

    def model_for(self, tier: Tier) -> str:
        """The alias, or the tier itself — never a refusal, unlike the direct client."""
        return self._models.get(tier, tier)


def _without_openai_path(url: str) -> str:
    """``/v1`` is the OpenAI-shaped route; the SDK appends its own ``/v1/messages``."""
    trimmed = url.rstrip("/")
    return trimmed[: -len("/v1")].rstrip("/") if trimmed.endswith("/v1") else trimmed
