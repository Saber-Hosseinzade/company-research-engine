"""Offline tests for the HTTP API and question page (plan-m6). The agent and page lookup are faked."""

import logging
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.agent import Agent
from app.api import STATIC, AgentFactory, PageLookup, agent_factory, app, highlight, page_lookup
from app.llm import ModelNotConfigured, ModelOutputError
from app.note import Citation, Claim, Note
from app.store import DatabaseUnavailable, PageView

NOTE = Note(
    status="answered",
    summary="Revenue grew.",
    claims=[Claim(text="Revenue grew.", citations=[Citation(accession="a-1", page=3, excerpt="grew")])],
    disclaimer="Analysis, not advice.",
)


class FakeAgent:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def answer(self, ticker: str, question: str) -> Note:
        self.calls.append((ticker, question))
        if self.error is not None:
            raise self.error
        return NOTE


class Factory:
    """Counts the agents it makes; each request must get a new one."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.agents: list[FakeAgent] = []

    def __call__(self) -> AbstractContextManager[Agent]:
        @contextmanager
        def make() -> Iterator[Agent]:
            agent = FakeAgent(self.error)
            self.agents.append(agent)
            yield cast(Agent, agent)

        return make()


PAGE = PageView(
    accession="0001045810-26-000010",
    page=40,
    ticker="NVDA",
    form="10-K",
    filing_date="2026-02-25",
    label="38",
    text="Data Center revenue\n  was up 142%. <script>alert(1)</script>",
    edgar_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000010/nvda.htm",
)


def lookup(accession: str, page: int) -> PageView | None:
    return PAGE if (accession, page) == (PAGE.accession, PAGE.page) else None


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(factory: AgentFactory | None = None, pages: PageLookup | None = None) -> None:
    if factory is not None:
        app.dependency_overrides[agent_factory] = lambda: factory
    if pages is not None:
        app.dependency_overrides[page_lookup] = lambda: pages


# /research (v0) and /invocations (AgentCore) share one handler (spec R3).
ROUTES = pytest.mark.parametrize("route", ["/research", "/invocations"])


def test_ping_follows_the_agentcore_contract(client: TestClient) -> None:
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "Healthy"}


@ROUTES
def test_research_returns_the_note(client: TestClient, route: str) -> None:
    factory = Factory()
    use(factory)
    response = client.post(route, json={"ticker": "nvda", "question": "How did revenue change?"})
    assert response.status_code == 200
    assert Note.model_validate(response.json()) == NOTE
    assert factory.agents[0].calls == [("nvda", "How did revenue change?")]


@pytest.mark.parametrize(
    "body",
    [
        {"ticker": "TSLA", "question": "Revenue?"},
        {"ticker": "NVDA", "question": "x" * 1001},
        {"ticker": "NVDA", "question": "   "},
    ],
)
@ROUTES
def test_out_of_scope_is_rejected_before_any_agent(
    client: TestClient, body: dict[str, Any], route: str
) -> None:
    factory = Factory()
    use(factory)
    response = client.post(route, json=body)
    assert response.status_code == 422
    assert factory.agents == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ModelOutputError("secret model text"), 502),
        (ModelNotConfigured("secret config text"), 503),
        (DatabaseUnavailable("secret db text"), 503),
        (RuntimeError("secret filing text"), 500),
    ],
)
@ROUTES
def test_errors_map_to_fixed_messages(client: TestClient, error: Exception, status: int, route: str) -> None:
    use(Factory(error))
    response = client.post(route, json={"ticker": "NVDA", "question": "Revenue?"})
    assert response.status_code == status
    assert "secret" not in response.text


def test_each_request_gets_its_own_agent(client: TestClient) -> None:
    factory = Factory()
    use(factory)
    for _ in range(2):
        client.post("/research", json={"ticker": "NVDA", "question": "Revenue?"})
    assert len(factory.agents) == 2
    assert factory.agents[0] is not factory.agents[1]


def test_page_view_escapes_text_marks_excerpt_and_links_edgar(client: TestClient) -> None:
    use(pages=lookup)
    response = client.get(f"/filings/{PAGE.accession}/pages/40", params={"excerpt": "revenue was UP 142%"})
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<mark id='excerpt'>revenue\n  was up 142%</mark>" in response.text
    assert PAGE.edgar_url in response.text
    assert "printed page 38" in response.text


def test_page_view_unknown_page_is_404(client: TestClient) -> None:
    use(pages=lookup)
    assert client.get(f"/filings/{PAGE.accession}/pages/41").status_code == 404


def test_page_view_database_down_is_503(client: TestClient) -> None:
    def down(accession: str, page: int) -> PageView | None:
        raise DatabaseUnavailable("db")

    use(pages=down)
    assert client.get(f"/filings/{PAGE.accession}/pages/40").status_code == 503


def test_highlight_without_match_is_plain_escaped_text() -> None:
    assert highlight("a <b> c", "missing") == "a &lt;b&gt; c"
    assert highlight("a <b> c", "") == "a &lt;b&gt; c"
    assert highlight("a <b> c", "<B>") == "a <mark id='excerpt'>&lt;b&gt;</mark> c"


def test_index_page_renders_text_only(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="ticker"' in response.text
    assert "innerHTML" not in (STATIC / "index.html").read_text(encoding="utf-8")


def test_logs_carry_no_question_filing_or_model_text(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    use(Factory(RuntimeError("secret filing text")), pages=lookup)
    client.post("/research", json={"ticker": "NVDA", "question": "secret question"})
    client.get(f"/filings/{PAGE.accession}/pages/40", params={"excerpt": "secret excerpt"})
    assert "research failed error=RuntimeError" in caplog.text
    assert "path=page status=200" in caplog.text
    assert not [r for r in caplog.records if r.exc_info]  # no traceback logged
    app_lines = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]  # not the test client
    assert not [line for line in app_lines if "secret" in line]
