"""Offline tests for the AgentCore Runtime client and the remote eval loop (AgentCore spec R19)."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_eval_v0 import ADVICE, FIGURE, PAGES, note

from app.eval_v0 import AnswerResult, _run
from app.note import Note, RunMeta
from app.runtime_client import RemoteError, RuntimeAnswerer, session_id


class FakeAgentCore:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status = body, status
        self.calls: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"statusCode": self.status, "response": io.BytesIO(self.body)}


ANSWER = note("Revenue was $130,497 million.").model_copy(
    update={"run": RunMeta(model="m", cost=0.002, prompt_tokens=10, retrieved=[("a-1", 40)])}
)


def test_one_session_per_run_and_note_parsed() -> None:
    client = FakeAgentCore(ANSWER.model_dump_json().encode())
    answerer = RuntimeAnswerer(client, "arn:aws:bedrock-agentcore:eu-central-1:1:runtime/r-1")
    first = answerer.answer("NVDA", "Revenue?")
    answerer.answer("AMD", "Revenue?")
    assert first.note == ANSWER
    assert first.retrieved == [("a-1", 40)]
    assert client.calls[0]["runtimeSessionId"] == client.calls[1]["runtimeSessionId"]
    assert len(client.calls[0]["runtimeSessionId"]) >= 33
    assert json.loads(client.calls[0]["payload"]) == {"ticker": "NVDA", "question": "Revenue?"}


def test_a_reply_that_is_not_a_note_is_an_error() -> None:
    with pytest.raises(ValidationError):
        RuntimeAnswerer(FakeAgentCore(b'{"status": "answered", "extra": "x"}'), "arn").note("NVDA", "q")


def test_non_200_is_an_error() -> None:
    with pytest.raises(RemoteError):
        RuntimeAnswerer(FakeAgentCore(b"{}", status=500), "arn").note("NVDA", "q")


def test_session_ids_are_long_enough_and_unique() -> None:
    assert len(session_id("invoke")) >= 33
    assert session_id("eval") != session_id("eval")


def test_run_scores_remote_answers_and_counts_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def answer(ticker: str, question: str) -> AnswerResult:
        if ticker == "NVDA" and "buy" in question:
            raise RemoteError("runtime answered HTTP 424")
        return AnswerResult(ANSWER, retrieved=[])

    exit_code = _run([FIGURE, ADVICE], answer, PAGES, "runtime r-1", lambda: None, tmp_path)
    out = capsys.readouterr().out
    assert "errors 1" in out
    assert "cost $0.0020" in out
    saved = json.loads(next((tmp_path / "evals").glob("answers-runtime_r-1-*.json")).read_text())
    assert [row["error"] for row in saved["items"]] == [None, "RemoteError"]
    assert Note.model_validate(saved["items"][0]["note"]) == ANSWER
    assert exit_code == 1  # the errored unanswerable item fails the not-found target
