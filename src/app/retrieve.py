"""Hybrid retrieval over one company's chunks: vector search plus full-text search, merged by
reciprocal rank fusion and collapsed to pages (the citation unit).

The company filter is in SQL, so a search can never return another company's page (S10).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.chunks import to_vector
from app.llm import Embedder
from app.store import Conn

Mode = Literal["vector", "fts", "hybrid"]
CANDIDATES = 30
RRF_K = 60

PageKey = tuple[str, int]


@dataclass(frozen=True)
class Hit:
    accession: str
    page: int
    score: float
    chunk_id: int


def vector_candidates(
    conn: Conn,
    ticker: str,
    strategy: str,
    query_vector: Sequence[float],
    model: str,
    n: int = CANDIDATES,
) -> list[tuple[int, str, int]]:
    # Only chunks embedded with the query's model: vectors from two models are not comparable.
    return [
        (int(i), str(a), int(p))
        for i, a, p in conn.execute(
            "SELECT id, accession, page_index FROM chunks"
            " WHERE ticker = %s AND strategy = %s AND embedding IS NOT NULL AND embedding_model = %s"
            " ORDER BY embedding <=> %s::vector, id LIMIT %s",
            (ticker, strategy, model, to_vector(query_vector), n),
        ).fetchall()
    ]


def fts_candidates(
    conn: Conn, ticker: str, strategy: str, query: str, n: int = CANDIDATES
) -> list[tuple[int, str, int]]:
    # Any query term may match (OR), ranked by ts_rank; plainto_tsquery alone would require every term.
    return [
        (int(i), str(a), int(p))
        for i, a, p in conn.execute(
            "WITH q AS (SELECT NULLIF(replace(plainto_tsquery('english', %s)::text, '&', '|'), '')"
            "::tsquery AS tq)"
            " SELECT id, accession, page_index FROM chunks, q"
            " WHERE ticker = %s AND strategy = %s AND q.tq IS NOT NULL AND tsv @@ q.tq"
            " ORDER BY ts_rank(tsv, q.tq) DESC, id LIMIT %s",
            (query, ticker, strategy, n),
        ).fetchall()
    ]


def fuse(rankings: Sequence[Sequence[tuple[int, str, int]]], k: int = 10) -> list[Hit]:
    """Reciprocal rank fusion over chunk rankings, then the best chunk per page, top k pages."""
    scores: dict[int, float] = {}
    where: dict[int, PageKey] = {}
    for ranking in rankings:
        for rank, (chunk_id, accession, page) in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            where[chunk_id] = (accession, page)
    best: dict[PageKey, Hit] = {}
    for chunk_id, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        key = where[chunk_id]
        if key not in best:
            best[key] = Hit(key[0], key[1], score, chunk_id)
    return list(best.values())[:k]


def search(
    conn: Conn,
    ticker: str,
    strategy: str,
    query: str,
    query_vector: Sequence[float] | None,
    model: str | None,
    mode: Mode = "hybrid",
    k: int = 10,
) -> list[Hit]:
    """Top k pages for the query. `query_vector` must come from `model`, the chunks' embedding model."""
    rankings = []
    if mode in ("vector", "hybrid"):
        if query_vector is None or model is None:
            raise ValueError("vector search needs a query vector and its model")
        rankings.append(vector_candidates(conn, ticker.upper(), strategy, query_vector, model))
    if mode in ("fts", "hybrid"):
        rankings.append(fts_candidates(conn, ticker.upper(), strategy, query))
    return fuse(rankings, k=k)


# --- Pages for the agent (M5) ----------------------------------------------------------------------

AGENT_STRATEGY = "window"  # kept in M4 (recall@10 0.863 with Titan, hybrid)
PAGES_FOR_DRAFT = 8


@dataclass(frozen=True)
class RetrievedPage:
    accession: str
    page: int
    ticker: str
    form: str
    filing_date: str
    report_date: str | None
    amendment_no: int | None
    label: str | None
    section: str | None
    text: str


class Retriever(Protocol):
    def retrieve(self, ticker: str, queries: Sequence[str]) -> list[RetrievedPage]: ...


def merge_page_rankings(rankings: Sequence[Sequence[Hit]], n: int = PAGES_FOR_DRAFT) -> list[PageKey]:
    """Fuse the page rankings of several queries by reciprocal rank; best n pages."""
    scores: dict[PageKey, float] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = (hit.accession, hit.page)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return [key for key, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))][:n]


@dataclass
class DbRetriever:
    """Hybrid search for each query, fused across queries, with page text and filing details."""

    conn: Conn
    embedder: Embedder
    strategy: str = AGENT_STRATEGY
    k: int = 10
    n_pages: int = PAGES_FOR_DRAFT

    def retrieve(self, ticker: str, queries: Sequence[str]) -> list[RetrievedPage]:
        vectors = self.embedder.embed(list(queries))
        rankings = [
            search(self.conn, ticker, self.strategy, q, v, self.embedder.model, mode="hybrid", k=self.k)
            for q, v in zip(queries, vectors, strict=True)
        ]
        keys = merge_page_rankings(rankings, self.n_pages)
        pages = []
        for accession, page in keys:
            row = self.conn.execute(
                "SELECT f.ticker, f.form, f.filing_date, f.report_date, f.amendment_no, p.label, p.section,"
                " p.text FROM pages p JOIN filings f USING (accession)"
                " WHERE p.accession = %s AND p.page_index = %s AND f.ticker = %s",
                (accession, page, ticker.upper()),
            ).fetchone()
            if row is not None:
                t, form, filed, report, amendment, label, section, text = row
                pages.append(
                    RetrievedPage(
                        accession, page, t, form, str(filed), str(report) if report else None,
                        amendment, label, section, text,
                    )
                )  # fmt: skip
        return pages
