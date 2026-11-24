"""Database tests for chunking, embedding and hybrid search (`make test-db`), with a fake embedder."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.chunks import EmbeddingMismatch, build_chunks, embed_chunks, embedding_dimension
from app.extract import extract_pages
from app.ingest import ingest
from app.pages import Filing
from app.retrieve import search

FIXTURE = Path(__file__).parents[1] / "tests" / "fixtures" / "mini_10k.html"


class FakeEmbedder:
    """3-dimensional vectors: a 'widget' axis, a 'revenue' axis and a constant."""

    def __init__(self, model: str = "fake", dims: int = 3) -> None:
        self.model, self.dims, self.calls = model, dims, 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for t in texts:
            low = t.lower()
            v = [float("widget" in low), float("revenue" in low), 0.1]
            out.append((v + [0.0] * self.dims)[: self.dims])
        return out


def filing(ticker: str, accession: str) -> Filing:
    meta: dict[str, Any] = {
        "ticker": ticker,
        "cik": 2488 if ticker == "AMD" else 1045810,
        "form": "10-K",
        "filing_date": "2026-02-04",
        "report_date": "2025-12-27",
        "accession": accession,
        "primary_document": "doc.htm",
    }
    return Filing(meta=meta, extraction=extract_pages(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def loaded(conn: psycopg.Connection) -> psycopg.Connection:
    ingest(conn, [filing("AMD", "0000002488-26-000018"), filing("NVDA", "0001045810-26-000021")])
    build_chunks(conn)
    return conn


def test_chunks_built_once_per_strategy(loaded: psycopg.Connection) -> None:
    counts: dict[str, int] = dict(
        loaded.execute("SELECT strategy, count(*) FROM chunks GROUP BY strategy").fetchall()
    )
    assert counts == {"page": 12, "window": 12}  # 6 short pages per filing, 2 filings
    assert build_chunks(loaded) == {"window": 0, "page": 0}  # nothing rebuilt


def test_embed_fills_only_missing_and_takes_dimension(loaded: psycopg.Connection) -> None:
    embedder = FakeEmbedder()
    assert embed_chunks(loaded, embedder) == 24
    assert embedding_dimension(loaded) == 3
    assert embed_chunks(loaded, embedder) == 0  # resume: nothing left


def test_other_model_or_dimension_stops(loaded: psycopg.Connection) -> None:
    embed_chunks(loaded, FakeEmbedder())
    loaded.execute("UPDATE chunks SET embedding = NULL WHERE id IN (SELECT id FROM chunks LIMIT 1)")
    with pytest.raises(EmbeddingMismatch, match="--reset"):
        embed_chunks(loaded, FakeEmbedder(model="other"))
    assert embed_chunks(loaded, FakeEmbedder(model="other", dims=4), reset=True) == 24
    assert embedding_dimension(loaded) == 4


@pytest.mark.parametrize("mode", ["vector", "fts", "hybrid"])
def test_search_finds_the_page_and_stays_in_company(loaded: psycopg.Connection, mode: Any) -> None:
    embed_chunks(loaded, FakeEmbedder())
    query = "Which widgets does the company make?"
    hits = search(loaded, "amd", "page", query, FakeEmbedder().embed([query])[0], "fake", mode=mode, k=3)
    assert (hits[0].accession, hits[0].page) == ("0000002488-26-000018", 3)
    assert all(h.accession == "0000002488-26-000018" for h in hits)


def test_refills_exactly_the_emptied_rows(loaded: psycopg.Connection) -> None:
    embed_chunks(loaded, FakeEmbedder())
    loaded.execute(
        "UPDATE chunks SET embedding = NULL WHERE id IN (SELECT id FROM chunks ORDER BY id LIMIT 5)"
    )
    embedder = FakeEmbedder()
    assert embed_chunks(loaded, embedder) == 5 and embedder.calls == 1
    missing = loaded.execute("SELECT count(*) FROM chunks WHERE embedding IS NULL").fetchone()
    assert missing == (0,)


def test_other_dimension_stops_with_reset_message(loaded: psycopg.Connection) -> None:
    embed_chunks(loaded, FakeEmbedder())
    loaded.execute("UPDATE chunks SET embedding = NULL WHERE id IN (SELECT id FROM chunks LIMIT 1)")
    with pytest.raises(EmbeddingMismatch, match="--reset"):
        embed_chunks(loaded, FakeEmbedder(dims=4))


def test_other_model_stops_before_any_api_call(loaded: psycopg.Connection) -> None:
    embed_chunks(loaded, FakeEmbedder())
    other = FakeEmbedder(model="other")
    with pytest.raises(EmbeddingMismatch, match="another model"):
        embed_chunks(loaded, other)
    assert other.calls == 0


def test_search_ignores_chunks_from_another_model(loaded: psycopg.Connection) -> None:
    embed_chunks(loaded, FakeEmbedder())
    query = "widgets"
    vec = FakeEmbedder().embed([query])[0]
    assert search(loaded, "AMD", "page", query, vec, "other", mode="vector") == []


def test_embed_before_chunk_creates_the_schema(conn: psycopg.Connection) -> None:
    assert embed_chunks(conn, FakeEmbedder()) == 0


def test_interrupted_embed_keeps_finished_batches(
    loaded: psycopg.Connection, reopen: Callable[[], psycopg.Connection]
) -> None:
    class FailsOnSecondBatch(FakeEmbedder):
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            if self.calls == 1:
                raise RuntimeError("HTTP 429")
            return super().embed(texts)

    with pytest.raises(RuntimeError, match="429"):
        embed_chunks(loaded, FailsOnSecondBatch(), batch=10)
    loaded.rollback()  # what `with connect()` does when the run fails
    saved = reopen().execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()
    assert saved == (10,)


def test_db_retriever_returns_pages_of_one_company_with_details(loaded: psycopg.Connection) -> None:
    from app.retrieve import DbRetriever

    embedder = FakeEmbedder()
    embed_chunks(loaded, embedder)
    pages = DbRetriever(loaded, embedder, strategy="window", n_pages=3).retrieve(
        "amd", ["widgets", "revenue"]
    )
    assert pages and all(p.accession == "0000002488-26-000018" and p.ticker == "AMD" for p in pages)
    assert 3 in {p.page for p in pages}  # the "widgets" page; the fake embedder ties the rest
    first = pages[0]
    assert (first.form, first.filing_date, first.report_date) == ("10-K", "2026-02-04", "2025-12-27")
    assert first.text  # full page text, not a chunk
