"""Model access through OpenRouter's OpenAI-compatible API. The only module that knows model names (A1).

Every call has a timeout, a retry limit (the SDK retries 429 and 5xx with backoff) and records token
usage (A2). M4 adds embeddings; M5 adds structured chat with provider routing that excludes
providers which train on or retain prompts.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 5
EMBED_BATCH = 32


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class Usage:
    requests: int = 0
    tokens: int = 0
    seconds: float = 0.0
    rate_limit_waits: int = 0


class ModelNotConfigured(RuntimeError):
    pass


@dataclass
class OpenRouterEmbedder:
    """Embeddings in batches of EMBED_BATCH, returned in input order."""

    client: Any  # openai.OpenAI, or a fake with .embeddings.create in tests
    model: str
    usage: Usage = field(default_factory=Usage)

    sleep: Any = time.sleep  # replaced in tests

    def _create(self, batch: list[str]) -> Any:
        for wait in (*RATE_LIMIT_WAITS, None):
            try:
                return self.client.embeddings.create(model=self.model, input=batch)
            except Exception as exc:
                if wait is None or not _is_rate_limit(exc):
                    raise
                log.warning("rate limited; waiting %.0f s", wait)
                self.usage.rate_limit_waits += 1
                self.sleep(wait)
        raise AssertionError("unreachable")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = list(texts[start : start + EMBED_BATCH])
            began = time.monotonic()
            response = self._create(batch)
            data = sorted(response.data, key=lambda d: d.index)
            if len(data) != len(batch):
                raise RuntimeError(f"embedding API returned {len(data)} vectors for {len(batch)} inputs")
            vectors.extend(list(d.embedding) for d in data)
            tokens = getattr(getattr(response, "usage", None), "prompt_tokens", 0) or 0
            self.usage.requests += 1
            self.usage.tokens += int(tokens)
            self.usage.seconds += time.monotonic() - began
            log.info("embedded inputs=%d tokens=%d", len(batch), tokens)
        return vectors


RATE_LIMIT_WAITS = (15.0, 30.0, 60.0, 120.0)  # seconds, after the SDK's own short retries


def _is_rate_limit(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429 or type(exc).__name__ == "RateLimitError"


def openrouter_client(settings: Settings) -> Any:
    from openai import OpenAI

    if not settings.openrouter_api_key:
        raise ModelNotConfigured("OPENROUTER_API_KEY is not set in .env.local.")
    return OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )


def get_embedder(settings: Settings | None = None) -> OpenRouterEmbedder:
    settings = settings or get_settings()
    if not settings.embedding_model_id:
        raise ModelNotConfigured("EMBEDDING_MODEL_ID is not set in .env.local.")
    return OpenRouterEmbedder(client=openrouter_client(settings), model=settings.embedding_model_id)


# --- Chat (M5) -------------------------------------------------------------------------------------

CHAT_TIMEOUT_SECONDS = 120.0
CHAT_MAX_TOKENS = 4000

# OpenRouter provider routing, applied to every chat request (spec concern 6): no provider that
# trains on prompts, zero-data-retention endpoints only, and only providers that support every
# parameter we send (so structured output is never silently dropped).
PROVIDER_ROUTING = {"data_collection": "deny", "zdr": True, "require_parameters": True}

T = TypeVar("T", bound=BaseModel)


@dataclass
class ChatUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0  # OpenRouter credits (USD), from usage.cost
    seconds: float = 0.0
    rate_limit_waits: int = 0
    parse_retries: int = 0


class ModelOutputError(RuntimeError):
    """Model output failed schema validation twice (spec R18)."""


class ChatModel(Protocol):
    model: str
    usage: ChatUsage

    def structured(self, system: str, user: str, schema: type[T], name: str) -> T: ...


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema as providers accept it: `oneOf` (Pydantic's tagged unions) becomes
    `anyOf` and `discriminator` is dropped. Our side still validates replies with the Pydantic model."""

    def convert(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                ("anyOf" if k == "oneOf" else k): convert(v) for k, v in node.items() if k != "discriminator"
            }
        if isinstance(node, list):
            return [convert(v) for v in node]
        return node

    result: dict[str, Any] = convert(model.model_json_schema())
    return result


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _error_summary(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()[:10]
    )


@dataclass
class OpenRouterChat:
    """Structured chat completions: the reply must parse into the given Pydantic model (S11)."""

    client: Any  # openai.OpenAI, or a fake with .chat.completions.create in tests
    model: str
    usage: ChatUsage = field(default_factory=ChatUsage)
    sleep: Any = time.sleep  # replaced in tests
    strict: bool = True

    def _create(self, messages: list[dict[str, str]], schema: type[BaseModel], name: str) -> Any:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": self.strict, "schema": provider_schema(schema)},
        }
        for wait in (*RATE_LIMIT_WAITS, None):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=CHAT_MAX_TOKENS,
                    # No temperature: none of the providers for our models accepts it, and with
                    # require_parameters an unsupported parameter rules out every provider.
                    extra_body={"provider": PROVIDER_ROUTING},
                )
            except Exception as exc:
                if wait is None or not _is_rate_limit(exc):
                    raise
                log.warning("rate limited; waiting %.0f s", wait)
                self.usage.rate_limit_waits += 1
                self.sleep(wait)
        raise AssertionError("unreachable")

    def _record(self, response: Any, began: float) -> None:
        usage = getattr(response, "usage", None)
        self.usage.calls += 1
        self.usage.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.usage.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        self.usage.cost += float(getattr(usage, "cost", 0.0) or 0.0)
        self.usage.seconds += time.monotonic() - began

    def structured(self, system: str, user: str, schema: type[T], name: str) -> T:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in (1, 2):
            began = time.monotonic()
            response = self._create(messages, schema, name)
            self._record(response, began)
            choices = getattr(response, "choices", None) or []
            content = (choices[0].message.content or "") if choices else ""  # no choice: a parse failure
            try:
                return schema.model_validate_json(_strip_fences(content))
            except ValidationError as exc:
                log.info("model output failed validation name=%s attempt=%d", name, attempt)
                if attempt == 2:
                    raise ModelOutputError(f"{name}: model output failed validation twice") from None
                self.usage.parse_retries += 1
                messages += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Your reply did not match the required JSON schema: "
                        f"{_error_summary(exc)}. Reply again with JSON only, matching the schema.",
                    },
                ]
        raise AssertionError("unreachable")


def get_chat(settings: Settings | None = None) -> OpenRouterChat:
    settings = settings or get_settings()
    if settings.model_provider != "openrouter":
        raise ModelNotConfigured(
            f"MODEL_PROVIDER={settings.model_provider} is not built in v0; set MODEL_PROVIDER=openrouter."
        )
    if not settings.model_id:
        raise ModelNotConfigured("MODEL_ID is not set in .env.local.")
    from openai import OpenAI

    if not settings.openrouter_api_key:
        raise ModelNotConfigured("OPENROUTER_API_KEY is not set in .env.local.")
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=CHAT_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )
    return OpenRouterChat(client=client, model=settings.model_id)
