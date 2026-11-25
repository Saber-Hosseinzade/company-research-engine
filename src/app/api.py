"""HTTP API and question page for the filings agent (spec R5, R13-R15, R17).

- `POST /research` answers one question with a fresh agent per request, so each request has its own
  usage counters and concurrent requests never mix tokens (plan-m6).
- `GET /filings/<accession>/pages/<n>` shows a stored page as escaped text with the excerpt marked (R14).
- `GET /` serves the static question page; `GET /ping` is a health check.

Responses and logs never carry model output in an error, and logs carry no question or filing text (R17).
Nothing identifying the user is sent to OpenRouter (spec concern 6).
"""

import html
import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.agent import Agent, ScopeError, check_scope
from app.llm import ModelNotConfigured, ModelOutputError
from app.note import Note
from app.store import DatabaseUnavailable, PageView, page_view

log = logging.getLogger(__name__)
# Uvicorn configures only its own loggers; this shows the app's id/count/timing lines (R17).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"

AgentFactory = Callable[[], AbstractContextManager[Agent]]
PageLookup = Callable[[str, int], PageView | None]


class ResearchRequest(BaseModel):
    ticker: str
    question: str


@contextmanager
def db_agent() -> Iterator[Agent]:
    """A new agent with its own chat client, embedder and connection, closed after the request."""
    from app.llm import get_chat, get_embedder
    from app.retrieve import DbRetriever
    from app.store import DbSources, connect

    chat, embedder = get_chat(), get_embedder()
    with connect() as conn:
        yield Agent(chat=chat, retriever=DbRetriever(conn, embedder), sources=DbSources(conn))


def db_page(accession: str, page: int) -> PageView | None:
    from app.store import connect

    with connect() as conn:
        return page_view(conn, accession, page)


def agent_factory() -> AgentFactory:
    return db_agent


def page_lookup() -> PageLookup:
    return db_page


app = FastAPI(title="company-research-engine", docs_url=None, redoc_url=None)


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html")


@app.post("/research")
def research(body: ResearchRequest, factory: Annotated[AgentFactory, Depends(agent_factory)]) -> Note:
    began = time.monotonic()
    status = 200
    try:
        try:
            check_scope(body.ticker, body.question)  # before any model call or connection (R1, R12)
        except ScopeError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            with factory() as agent:
                return agent.answer(body.ticker, body.question)
        except ModelOutputError:
            raise HTTPException(502, "The model returned output that could not be read.") from None
        except ModelNotConfigured:
            raise HTTPException(503, "The model is not configured on the server.") from None
        except DatabaseUnavailable:
            raise HTTPException(503, "The filings database is not reachable.") from None
        except Exception as exc:
            # Type only: a message or traceback could carry model or filing text (R17).
            log.error("research failed error=%s", type(exc).__name__)
            raise HTTPException(500, "The research run failed.") from None
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        log.info("request path=research status=%d seconds=%.1f", status, time.monotonic() - began)


def highlight(text: str, excerpt: str) -> str:
    """Escaped page text with the first match of `excerpt` in <mark>; whitespace and case tolerant."""
    words = excerpt.split()
    match = None
    if words:
        pattern = r"\s+".join(re.escape(w) for w in words)
        match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return html.escape(text)
    start, end = match.span()
    return (
        html.escape(text[:start])
        + "<mark id='excerpt'>"
        + html.escape(text[start:end])
        + "</mark>"
        + html.escape(text[end:])
    )


@app.get("/filings/{accession}/pages/{page}", response_class=HTMLResponse)
def filing_page(
    accession: str,
    page: int,
    lookup: Annotated[PageLookup, Depends(page_lookup)],
    excerpt: Annotated[str, Query(max_length=5000)] = "",
) -> HTMLResponse:
    try:
        view = lookup(accession, page)
    except DatabaseUnavailable:
        log.info("request path=page status=503")
        raise HTTPException(503, "The filings database is not reachable.") from None
    if view is None:
        log.info("request path=page status=404")
        raise HTTPException(404, "No such filing page.")
    log.info("request path=page status=200")
    e = html.escape
    label = f", printed page {e(view.label)}" if view.label else ""
    title = f"{e(view.ticker)} {e(view.form)} filed {e(view.filing_date)}, page {view.page}{label}"
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{title}</title>
<style>
body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 60rem; padding: 1rem; }}
pre {{ white-space: pre-wrap; font: 13px/1.45 ui-monospace, monospace; background: #f6f6f4;
      padding: 1rem; border-radius: 6px; }}
mark {{ background: #ffe08a; }}
</style></head><body>
<p><a href="/">&larr; Ask a question</a></p>
<h1 style="font-size:1.2rem">{title}</h1>
<p>Accession {e(view.accession)}. Stored text of the filing page (page index {view.page}).
<a href="{e(view.edgar_url)}" rel="noopener noreferrer" target="_blank">Original filing on EDGAR</a></p>
<pre>{highlight(view.text, excerpt)}</pre>
<script>document.getElementById("excerpt")?.scrollIntoView({{block: "center"}});</script>
</body></html>"""
    return HTMLResponse(body)
