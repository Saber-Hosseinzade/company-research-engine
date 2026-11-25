"""Call the deployed agent on AgentCore Runtime (`InvokeAgentRuntime`, SigV4) (AgentCore spec R16, R19).

`RuntimeAnswerer` is the eval's remote answerer: one runtime session per eval run, each reply parsed as
`Note` (S11: a reply that is not a valid note is an error, never trusted). `make invoke` uses `main`.
"""

import json
import time
import uuid
from typing import Any

from app.eval_v0 import AnswerResult
from app.note import Note

READ_TIMEOUT = 300  # seconds; a cold first request (runtime start plus Aurora resume) can take ~45 s


class RemoteError(RuntimeError):
    pass


def runtime_client(region: str) -> Any:
    import boto3
    from botocore.config import Config

    config = Config(read_timeout=READ_TIMEOUT, retries={"mode": "standard", "max_attempts": 2})
    return boto3.client("bedrock-agentcore", region_name=region, config=config)


def session_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"  # AgentCore needs at least 33 characters


class RuntimeAnswerer:
    def __init__(self, client: Any, runtime_arn: str, session: str | None = None) -> None:
        self.client, self.runtime_arn = client, runtime_arn
        self.session = session or session_id("eval")

    def note(self, ticker: str, question: str) -> Note:
        response = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=self.session,
            payload=json.dumps({"ticker": ticker, "question": question}).encode(),
            contentType="application/json",
            accept="application/json",
        )
        status = int(response.get("statusCode", 200))
        if status != 200:
            raise RemoteError(f"runtime answered HTTP {status}")
        return Note.model_validate_json(response["response"].read())

    def answer(self, ticker: str, question: str) -> AnswerResult:
        note = self.note(ticker, question)
        return AnswerResult(note, retrieved=note.run.retrieved if note.run else [])


def main(argv: list[str] | None = None) -> int:
    import argparse

    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Ask the deployed agent one question.")
    parser.add_argument("--runtime-arn", required=True)
    parser.add_argument("ticker")
    parser.add_argument("question")
    args = parser.parse_args(argv)
    answerer = RuntimeAnswerer(
        runtime_client(get_settings().aws_region), args.runtime_arn, session_id("invoke")
    )
    began = time.monotonic()
    note = answerer.note(args.ticker, args.question)
    print(note.model_dump_json(indent=1))
    print(f"seconds={time.monotonic() - began:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
