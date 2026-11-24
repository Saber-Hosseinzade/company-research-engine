"""Hybrid retrieval over one company's chunks: vector search plus full-text search, merged by
reciprocal rank fusion and collapsed to pages (the citation unit).

The company filter is in SQL, so a search can never return another company's page (S10).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import psycopg

from app.chunks import to_vector

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
    conn: psycopg.Connection,
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
    conn: psycopg.Connection, ticker: str, strategy: str, query: str, n: int = CANDIDATES
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
    conn: psycopg.Connection,
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
