"""Offline tests for the embedding client (a fake API, no network, A10)."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.llm import ModelNotConfigured, OpenRouterEmbedder, get_embedder


class FakeEmbeddings:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def create(self, model: str, input: list[str]) -> Any:
        self.batches.append(input)
        # Return out of order to check that the client sorts by index.
        data = [SimpleNamespace(index=i, embedding=[float(len(t)), float(i)]) for i, t in enumerate(input)]
        return SimpleNamespace(data=list(reversed(data)), usage=SimpleNamespace(prompt_tokens=len(input) * 3))


def test_batches_order_and_usage() -> None:
    fake = FakeEmbeddings()
    embedder = OpenRouterEmbedder(client=SimpleNamespace(embeddings=fake), model="m")
    texts = [f"t{i:02d}" for i in range(70)]
    vectors = embedder.embed(texts)
    assert [len(b) for b in fake.batches] == [32, 32, 6]
    assert vectors[0] == [3.0, 0.0] and vectors[33] == [3.0, 1.0] and len(vectors) == 70
    assert (embedder.usage.requests, embedder.usage.tokens) == (3, 210)


def test_short_response_is_an_error() -> None:
    class Short(FakeEmbeddings):
        def create(self, model: str, input: list[str]) -> Any:
            return SimpleNamespace(data=[], usage=None)

    embedder = OpenRouterEmbedder(client=SimpleNamespace(embeddings=Short()), model="m")
    with pytest.raises(RuntimeError, match="0 vectors for 1 inputs"):
        embedder.embed(["x"])


def test_missing_settings_stop_with_a_message() -> None:
    with pytest.raises(ModelNotConfigured, match="EMBEDDING_MODEL_ID"):
        get_embedder(Settings(_env_file=None, openrouter_api_key="k"))
    with pytest.raises(ModelNotConfigured, match="OPENROUTER_API_KEY"):
        get_embedder(Settings(_env_file=None, embedding_model_id="m", openrouter_api_key=None))
