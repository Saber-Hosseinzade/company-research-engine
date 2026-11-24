"""The v0 filings agent: one LangGraph graph (spec Design, A4, A5).

scope (code) -> plan_queries (model) -> retrieve (code) -> draft (model) -> validate (code)
  -> [more than half the claims dropped and one draft so far: draft again with the reasons] -> finish.

The model only plans queries and drafts claims; scope, retrieval, every citation and number check,
arithmetic, period labels and the advice filter are code. Dependencies are injected so tests run the
graph offline with fakes (A10). No checkpointer or memory in v0.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.draft import Draft
from app.edgar import TICKERS
from app.llm import ChatModel, ModelOutputError
from app.note import Note, RunMeta
from app.policy import DISCLAIMER
from app.prompts import (
    DRAFT_SYSTEM,
    PLAN_SYSTEM,
    PROMPT_VERSION,
    QueryPlan,
    draft_user,
    plan_user,
    retry_feedback,
)
from app.retrieve import RetrievedPage, Retriever
from app.validate import Sources, validate

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 1000  # spec R12
MAX_DRAFTS = 2  # one re-draft when validation drops more than half (A5)


class ScopeError(ValueError):
    """The request is out of scope (R1, R12); raised before any model call."""


class State(TypedDict, total=False):
    ticker: str
    question: str
    queries: list[str]
    pages: list[RetrievedPage]
    draft: Draft
    drafts: int
    feedback: str
    note: Note


def check_scope(ticker: str, question: str) -> tuple[str, str]:
    ticker = ticker.strip().upper()
    if ticker not in TICKERS:
        raise ScopeError(f"ticker must be one of {', '.join(TICKERS)}")
    question = question.strip()
    if not question:
        raise ScopeError("question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ScopeError(f"question is longer than {MAX_QUESTION_CHARS} characters")
    return ticker, question


@dataclass
class Agent:
    chat: ChatModel
    retriever: Retriever
    sources: Sources

    def __post_init__(self) -> None:
        graph = StateGraph(State)
        graph.add_node("plan_queries", self._plan)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("draft", self._draft)
        graph.add_node("validate", self._validate)
        graph.add_edge(START, "plan_queries")
        graph.add_edge("plan_queries", "retrieve")
        graph.add_edge("retrieve", "draft")
        graph.add_edge("draft", "validate")
        graph.add_conditional_edges("validate", self._after_validate, {"draft": "draft", "finish": END})
        self._graph = graph.compile()

    # --- nodes ---

    def _plan(self, state: State) -> dict[str, Any]:
        plan = self.chat.structured(
            PLAN_SYSTEM, plan_user(state["ticker"], state["question"]), QueryPlan, "plan"
        )
        queries = [q.strip() for q in plan.queries if q.strip()][:4]
        return {"queries": [state["question"], *queries]}  # the question itself is always searched

    def _retrieve(self, state: State) -> dict[str, Any]:
        return {"pages": self.retriever.retrieve(state["ticker"], state["queries"])}

    def _draft(self, state: State) -> dict[str, Any]:
        drafts = state.get("drafts", 0) + 1
        if not state["pages"]:
            return {"draft": Draft(status="not_found"), "drafts": drafts}
        user = draft_user(state["ticker"], state["question"], state["pages"])
        if state.get("feedback"):
            user += "\n\n" + state["feedback"]
        try:
            draft = self.chat.structured(DRAFT_SYSTEM, user, Draft, "draft")
        except ModelOutputError:
            if state.get("note") is None:
                raise  # the first draft failed: the run returns an error (R18)
            # A failed re-draft never makes the answer worse: keep the previous draft, whose note the
            # next validation reproduces; the draft count is spent, so the graph then finishes.
            log.info("re-draft failed parsing; keeping the first draft")
            return {"drafts": drafts}
        return {"draft": draft, "drafts": drafts}

    def _validate(self, state: State) -> dict[str, Any]:
        note = validate(state["draft"], state["ticker"], self.sources)
        claim_drops = sum(n for reason, n in note.dropped.items() if reason != "summary_dropped")
        previous = state.get("note")
        # A re-draft never makes the answer worse: keep the note with more surviving claims.
        best = previous if previous is not None and len(previous.claims) > len(note.claims) else note
        update: dict[str, Any] = {"note": best}
        if state["drafts"] < MAX_DRAFTS and claim_drops * 2 > len(state["draft"].claims):
            update["feedback"] = retry_feedback(
                {k: v for k, v in note.dropped.items() if k != "summary_dropped"}
            )
        else:
            update["feedback"] = ""
        return update

    def _after_validate(self, state: State) -> str:
        return "draft" if state.get("feedback") else "finish"

    # --- entry point ---

    def answer(self, ticker: str, question: str) -> Note:
        ticker, question = check_scope(ticker, question)
        began = time.monotonic()
        before = (self.chat.usage.prompt_tokens, self.chat.usage.completion_tokens, self.chat.usage.cost)
        retries_before = self.chat.usage.parse_retries
        embed_usage = getattr(getattr(self.retriever, "embedder", None), "usage", None)
        embed_before = getattr(embed_usage, "tokens", 0)
        state: State = self._graph.invoke({"ticker": ticker, "question": question})  # type: ignore[assignment]
        note = state.get("note") or Note(status="not_found", disclaimer=DISCLAIMER)
        note.run = RunMeta(
            model=self.chat.model,
            prompt_version=PROMPT_VERSION,
            prompt_tokens=self.chat.usage.prompt_tokens - before[0],
            completion_tokens=self.chat.usage.completion_tokens - before[1],
            cost=round(self.chat.usage.cost - before[2], 6),
            embedding_tokens=getattr(embed_usage, "tokens", 0) - embed_before,
            seconds=round(time.monotonic() - began, 2),
            drafts=state.get("drafts", 0),
            parse_retries=self.chat.usage.parse_retries - retries_before,
            retrieved=[(p.accession, p.page) for p in state.get("pages", [])],
        )
        log.info(
            "answered ticker=%s status=%s claims=%d dropped=%d drafts=%d tokens=%d seconds=%.1f",
            ticker,
            note.status,
            len(note.claims),
            sum(note.dropped.values()),
            note.run.drafts,
            note.run.prompt_tokens + note.run.completion_tokens,
            note.run.seconds,
        )
        return note
