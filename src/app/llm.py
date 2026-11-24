"""Model access through OpenRouter's OpenAI-compatible API. The only module that knows model names (A1).

Every call has a timeout, a retry limit (the SDK retries 429 and 5xx with backoff) and records token
usage (A2). M4 adds embeddings; M5 adds chat.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

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
