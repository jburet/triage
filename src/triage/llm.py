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
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias, TypeVar, cast, get_origin

import httpx
import structlog
from pydantic import BaseModel

from triage.config import LLMProvider, Settings

Tier: TypeAlias = Literal["triage", "analysis", "diagnosis"]

log = structlog.get_logger(__name__)

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
MODELS_TIMEOUT = 10.0
"""A startup question, not a model call: it must not be what makes startup slow."""


def tool_name(schema: type[BaseModel]) -> str:
    """A tool name Anthropic accepts, derived from the schema being asked for."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__)[:64]


UNSUPPORTED_BY_STRICT = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
)
"""Value constraints strict tool use rejects outright, and Pydantic keeps enforcing.

Dropping them from the wire loses nothing: the API is asked to guarantee the
*structure* of the tool input, and `model_validate` still refuses a rank_score
of 2 or an empty causes list on the way in.
"""


def _objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object in a schema: itself, its `$defs`, and its properties' items."""
    found = [schema] if "properties" in schema else []
    for value in list(schema.get("$defs", {}).values()) + list(
        schema.get("properties", {}).values()
    ):
        if isinstance(value, dict):
            found.extend(_objects(value))
    items = schema.get("items")
    if isinstance(items, dict):
        found.extend(_objects(items))
    return found


def _expressible_strictly(schema: dict[str, Any]) -> bool:
    """Strict lists every property as required, so a schema with an optional one cannot say it.

    Forcing the optional field into `required` would make the model fill something
    the schema says it may leave out, which is a different request than the one
    the node wrote.
    """
    return all(
        set(obj.get("properties", {})) == set(obj.get("required", [])) for obj in _objects(schema)
    )


def _strictly(schema: dict[str, Any]) -> dict[str, Any]:
    for obj in _objects(schema):
        obj["additionalProperties"] = False
    for obj in _objects(schema):
        for keyword in UNSUPPORTED_BY_STRICT:
            obj.pop(keyword, None)
        for value in obj.get("properties", {}).values():
            if isinstance(value, dict):
                for keyword in UNSUPPORTED_BY_STRICT:
                    value.pop(keyword, None)
    for name in ("properties", "$defs"):
        for value in schema.get(name, {}).values():
            if isinstance(value, dict):
                for keyword in UNSUPPORTED_BY_STRICT:
                    value.pop(keyword, None)
    return schema


def _unenveloped(value: Any, name: str) -> Any:
    """Whatever arrived inside however many envelopes of the field's own name.

    Peeling one layer was not enough: on 2026-08-24 the envelope was doubled, and
    the answer left behind still failed the same field with the same message as
    the answer the unwrap was written for. Stopping short looks identical to not
    trying.
    """
    while isinstance(value, dict) and set(value) == {name}:
        value = value[name]
    return value


def _decoded(arguments: Any, schema: type[BaseModel]) -> Any:
    """Structured fields that arrived wrapped, unwrapped back to what they are.

    A tool call is supposed to carry ``input`` as an object, and on 2026-08-24 it
    sometimes did not. Two shapes were seen, both of them the whole answer folded
    into one field:

    - as **text** — a list field holding the model's own JSON with the tool-call
      markup still trailing it, ``[{…}]</causes>``. The leading value is decoded
      and the trailing markup dropped.
    - as **its own envelope** — ``requests`` holding ``{"requests": [...]}``, and
      once ``{"requests": {"requests": [...]}}``. Every layer of the field's own
      name is peeled, and only for a list field: a dict is what an object field
      is supposed to be, so reaching into one would be guessing.

    They arrive together — an envelope written as a string — so the decode feeds
    the peel rather than ending beside it.

    A field the schema already declares as a string is left alone either way, so
    nothing that legitimately *is* prose gets reinterpreted.

    Parsing, not repair: nothing is invented, the unwrap has to be unambiguous,
    and the schema still refuses whatever comes out (ADR-0022).
    """
    if not isinstance(arguments, dict):
        return arguments
    decoder = json.JSONDecoder()
    fields = schema.model_fields
    decoded = dict(arguments)
    for name, value in arguments.items():
        field_info = fields.get(name)
        if field_info is None or field_info.annotation is str:
            continue
        if isinstance(value, str):
            try:
                value, _ = decoder.raw_decode(value.strip())
            except ValueError:
                continue
            decoded[name] = value
        # Whatever came out of the text is looked at the same way as what came
        # in: the two shapes arrived together, an envelope written as a string.
        if isinstance(value, dict) and get_origin(field_info.annotation) is list:
            inner = _unenveloped(value, name)
            if not isinstance(inner, dict):
                decoded[name] = inner
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
        self._strict = True

    @staticmethod
    def _refuses_strict(exc: Exception) -> bool:
        """A 400 naming the field, and nothing else — never a blanket retry."""
        return getattr(exc, "status_code", None) == 400 and "strict" in str(exc)

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

    @property
    def configured_models(self) -> Mapping[Tier, str]:
        """The names chosen for us. Empty on the proxy means the tier is the name."""
        return self._models

    def model_for(self, tier: Tier) -> str:
        try:
            return self._models[tier]
        except KeyError as exc:
            raise LookupError(
                f"no model configured for tier {tier!r}: set TRIAGE_MODEL_{tier.upper()}"
            ) from exc

    def _tool(self, schema: type[BaseModel]) -> dict[str, Any]:
        """One tool whose input schema *is* the Pydantic schema, strict where it can be.

        Strict is what makes the API guarantee the tool input matches the schema.
        Without it, against the real `qualify` prompt, one answer in two came back
        with the whole reply serialised into the first field and the causes list
        missing — 3/8 valid; with it, 6/6 (ADR-0022).
        """
        json_schema = schema.model_json_schema()
        tool: dict[str, Any] = {
            "name": tool_name(schema),
            "description": (schema.__doc__ or f"Return a {schema.__name__}.").strip(),
            "input_schema": json_schema,
        }
        if self._strict and _expressible_strictly(json_schema):
            tool["input_schema"] = _strictly(json_schema)
            tool["strict"] = True
        return tool

    async def call(self, tier: Tier, prompt: str, schema: type[T]) -> T:
        name = tool_name(schema)
        request: dict[str, Any] = {
            "model": self.model_for(tier),
            "max_tokens": self._max_tokens,
            "tool_choice": {"type": "tool", "name": name},
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            message = await self._anthropic().messages.create(tools=[self._tool(schema)], **request)
        except Exception as exc:
            if not self._refuses_strict(exc):
                raise
            # A LiteLLM too old to know the field rejects the whole request rather
            # than ignoring it. Asked without it, the same proxy answers.
            log.warning("strict_tool_use_unsupported", detail=str(exc)[:200])
            self._strict = False
            message = await self._anthropic().messages.create(tools=[self._tool(schema)], **request)
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

    async def published_models(self) -> list[str] | None:
        """What the proxy says it serves, or ``None`` when it would not say.

        A name the proxy does not publish is a 400 at whatever hour that tier
        first runs, and the two tiers fail differently: the diagnosis tier's 400
        ends the run, the triage tier's is swallowed by the classifier's fallback
        and costs an alert the right collector recipe without saying so.

        Unreachable is not the same as wrong. A proxy that will not answer is no
        evidence about the names, so this answers ``None`` and the caller carries
        on rather than making a blip at 03:00 the thing that stops an incident.
        """
        url = f"{self.base_url}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=MODELS_TIMEOUT) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {self._api_key}"}
                )
                response.raise_for_status()
                data = response.json().get("data", [])
        except Exception as exc:
            log.warning("proxy_models_unreadable", url=url, error=str(exc))
            return None
        return [str(entry["id"]) for entry in data if isinstance(entry, dict) and "id" in entry]


def _without_openai_path(url: str) -> str:
    """``/v1`` is the OpenAI-shaped route; the SDK appends its own ``/v1/messages``."""
    trimmed = url.rstrip("/")
    return trimmed[: -len("/v1")].rstrip("/") if trimmed.endswith("/v1") else trimmed


MODEL_SETTING = {
    "triage": "model_triage",
    "analysis": "model_analysis",
    "diagnosis": "model_diagnosis",
}


def _configured_models(settings: Settings) -> tuple[dict[Tier, str], list[str]]:
    """The tier-to-model mapping from `TRIAGE_MODEL_*`, and which are unset."""
    models: dict[Tier, str] = {}
    missing: list[str] = []
    for tier, attribute in MODEL_SETTING.items():
        value = getattr(settings, attribute)
        if value:
            models[cast(Tier, tier)] = value
        else:
            missing.append(f"TRIAGE_{attribute.upper()}")
    return models, missing


def build_llm(settings: Settings) -> StructuredLLM:
    """The proxy, or the API directly — the same one method either way (ADR-0007).

    ``auto`` is what makes the local shortcut usable without being a footgun: it
    takes the direct client only when there is an Anthropic key and the LiteLLM
    URL is still the default, so a deployment that configures a proxy keeps its
    guardrails even if a key happens to be in the environment.

    It lives beside the two clients rather than beside the graph's wiring
    because the analysis sandbox needs it and needs nothing else Triage has:
    reaching it through ``runtime`` put the Jira, Slack and Datadog clients on
    the image's path for a factory function (M7 3.1).
    """
    provider = settings.llm_provider
    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if provider is LLMProvider.AUTO:
        # Compared against the field default rather than a fresh Settings(), which
        # would re-read the environment and call a configured proxy "untouched".
        untouched_proxy = settings.litellm_url == type(settings).model_fields["litellm_url"].default
        provider = LLMProvider.ANTHROPIC if key and untouched_proxy else LLMProvider.LITELLM
    models, missing = _configured_models(settings)

    if provider is LLMProvider.LITELLM:
        # Unset, the tier is the model name — what a proxy configured for Triage
        # publishes. Set, they are what a proxy nobody will re-configure for us
        # calls those models. Half-set is neither, and would fail on one tier at
        # whatever hour that node first runs.
        if models and missing:
            raise ValueError(
                f"a proxy addressed by model name needs all three tiers; unset: "
                f"{', '.join(missing)}. Leave all three empty to address the proxy "
                f"by the aliases triage / analysis / diagnosis instead."
            )
        # Logged because "model not found" from a proxy is otherwise a guess about
        # which of the two addressings is in force.
        log.info(
            "llm_proxy",
            url=settings.litellm_url,
            addressed_by="model name" if models else "tier alias",
        )
        return LiteLLMClient(settings.litellm_url, settings.litellm_api_key, models=models)

    if missing:
        raise ValueError(
            f"calling Anthropic directly needs a model per tier; unset: {', '.join(missing)}. "
            f"See .env.example for the current ids."
        )
    if not key:
        log.warning(
            "anthropic_key_unset",
            detail="no TRIAGE_ANTHROPIC_API_KEY and no ANTHROPIC_API_KEY; the SDK will "
            "resolve its own credentials (environment, or an `ant auth login` profile)",
        )
    log.info("llm_direct", detail="calling Anthropic directly: the proxy's spend caps do not apply")
    return AnthropicClient(settings.anthropic_api_key, models)


async def verify_models(llm: StructuredLLM) -> None:
    """Refuse a model name the proxy does not serve, before anything is collected.

    The sibling of ``build_llm``'s half-set refusal, and it has to be async and
    therefore separate: that one catches a tier nobody configured, this one a
    tier configured with a name this proxy never heard of. Both are the same
    mistake — a run that will fail on one tier, at whatever hour that node first
    runs, having already spent the collection.

    Only names we chose are checked. With ``TRIAGE_MODEL_*`` unset the tier *is*
    the name, and a proxy configured for Triage publishes those under a listing
    we have no claim on.
    """
    if not isinstance(llm, LiteLLMClient) or not llm.configured_models:
        return
    published = await llm.published_models()
    if published is None:
        return
    unknown = {tier: name for tier, name in llm.configured_models.items() if name not in published}
    if not unknown:
        return
    asked = ", ".join(f"TRIAGE_MODEL_{tier.upper()}={name}" for tier, name in unknown.items())
    raise ValueError(
        f"the proxy at {llm.base_url} does not serve {asked}. It publishes: {', '.join(published)}."
    )
