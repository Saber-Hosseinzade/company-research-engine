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


class RateLimited(Exception):
    status_code = 429


def test_rate_limit_waits_then_succeeds() -> None:
    class Flaky(FakeEmbeddings):
        failures = 2

        def create(self, model: str, input: list[str]) -> Any:
            if self.failures:
                self.failures -= 1
                raise RateLimited()
            return super().create(model, input)

    waits: list[float] = []
    embedder = OpenRouterEmbedder(client=SimpleNamespace(embeddings=Flaky()), model="m")
    embedder.sleep = waits.append
    assert len(embedder.embed(["a", "b"])) == 2
    assert waits == [15.0, 30.0] and embedder.usage.rate_limit_waits == 2


def test_rate_limit_gives_up_after_the_last_wait() -> None:
    class Always(FakeEmbeddings):
        def create(self, model: str, input: list[str]) -> Any:
            raise RateLimited()

    waits: list[float] = []
    embedder = OpenRouterEmbedder(client=SimpleNamespace(embeddings=Always()), model="m")
    embedder.sleep = waits.append
    with pytest.raises(RateLimited):
        embedder.embed(["a"])
    assert waits == [15.0, 30.0, 60.0, 120.0]


def test_other_errors_are_not_retried() -> None:
    class Broken(FakeEmbeddings):
        def create(self, model: str, input: list[str]) -> Any:
            raise ValueError("bad request")

    waits: list[float] = []
    embedder = OpenRouterEmbedder(client=SimpleNamespace(embeddings=Broken()), model="m")
    embedder.sleep = waits.append
    with pytest.raises(ValueError):
        embedder.embed(["a"])
    assert waits == []


# --- chat ---

from pydantic import BaseModel  # noqa: E402

from app.llm import PROVIDER_ROUTING, ModelOutputError, OpenRouterChat, get_chat  # noqa: E402


class Answer(BaseModel):
    queries: list[str]


class FakeCompletions:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        content = self.replies.pop(0)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, cost=0.0003)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage
        )


def chat(replies: list[str]) -> tuple[OpenRouterChat, FakeCompletions]:
    fake = FakeCompletions(replies)
    return OpenRouterChat(client=SimpleNamespace(chat=SimpleNamespace(completions=fake)), model="m"), fake


def test_request_carries_schema_and_routing() -> None:
    model, fake = chat(['{"queries": ["revenue 2025"]}'])
    assert model.structured("sys", "user", Answer, "plan").queries == ["revenue 2025"]
    request = fake.requests[0]
    assert request["response_format"]["json_schema"]["schema"] == Answer.model_json_schema()  # no union
    assert request["extra_body"] == {"provider": PROVIDER_ROUTING}
    assert PROVIDER_ROUTING == {"data_collection": "deny", "zdr": True, "require_parameters": True}
    assert "temperature" not in request  # unsupported by the providers; would block routing


def test_usage_is_summed_and_fences_are_stripped() -> None:
    model, _ = chat(['```json\n{"queries": []}\n```'])
    model.structured("s", "u", Answer, "plan")
    assert (model.usage.calls, model.usage.prompt_tokens, model.usage.completion_tokens) == (1, 100, 20)
    assert model.usage.cost == pytest.approx(0.0003)


def test_parse_failure_retries_once_with_the_error() -> None:
    model, fake = chat(['{"wrong": 1}', '{"queries": ["x"]}'])
    assert model.structured("s", "u", Answer, "plan").queries == ["x"]
    assert model.usage.parse_retries == 1
    retry_message = fake.requests[1]["messages"][-1]["content"]
    assert "did not match the required JSON schema" in retry_message and "queries" in retry_message


def test_second_parse_failure_is_an_error() -> None:
    model, _ = chat(["not json", "still not json"])
    with pytest.raises(ModelOutputError, match="failed validation twice"):
        model.structured("s", "u", Answer, "plan")


def test_bedrock_is_not_built_in_v0() -> None:
    with pytest.raises(ModelNotConfigured, match="not built in v0"):
        get_chat(Settings(_env_file=None, model_provider="bedrock"))


def test_provider_schema_has_no_oneof() -> None:
    import json

    from app.draft import Draft
    from app.llm import provider_schema

    text = json.dumps(provider_schema(Draft))
    assert '"oneOf"' not in text and '"discriminator"' not in text and '"anyOf"' in text
    assert '"oneOf"' in json.dumps(Draft.model_json_schema())  # what Pydantic emits


def test_empty_choices_count_as_a_parse_failure() -> None:
    class NoChoice(FakeCompletions):
        def create(self, **kwargs: Any) -> Any:
            self.requests.append(kwargs)
            if len(self.requests) == 1:
                return SimpleNamespace(choices=[], usage=None)
            return super().create(**kwargs)

    fake = NoChoice(['{"queries": ["x"]}', '{"queries": ["x"]}'])
    model = OpenRouterChat(client=SimpleNamespace(chat=SimpleNamespace(completions=fake)), model="m")
    assert model.structured("s", "u", Answer, "plan").queries == ["x"] and model.usage.parse_retries == 1
