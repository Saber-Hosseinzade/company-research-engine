"""Chunks for retrieval (spec R3, A9): two strategies, each bounded by one page, so every chunk maps to
exactly one citable page.

- window: line-aligned windows of about WINDOW_CHARS, with one line of overlap; lines (and so table
  rows) are never split.
- page: the whole page.

`make chunk` builds both from the `pages` table; `make embed` embeds the chunks that have no
embedding yet, so an interrupted run resumes where it stopped.
"""

import logging
import sys
import time
from collections.abc import Sequence

import psycopg

from app.ingest import apply_schema
from app.llm import Embedder

log = logging.getLogger(__name__)

STRATEGIES = ("window", "page")
WINDOW_CHARS = 1500


def window_chunks(text: str, limit: int = WINDOW_CHARS) -> list[str]:
    lines = [line for line in text.split("\n") if line.strip()]
    chunks: list[str] = []
    start = 0
    while start < len(lines):
        end, size = start, 0
        while end < len(lines) and (end == start or size + len(lines[end]) + 1 <= limit):
            size += len(lines[end]) + 1
            end += 1
        chunks.append("\n".join(lines[start:end]))
        if end >= len(lines):
            break
        # One line of overlap, always moving forward; none when the overlap line and the next line
        # together don't fit, since that chunk would hold only the overlap line again.
        overlap_fits = len(lines[end - 1]) + len(lines[end]) + 2 <= limit
        start = end - 1 if end - 1 > start and overlap_fits else end
    return chunks


def page_chunks(text: str) -> list[str]:
    return [text] if text.strip() else []


CHUNKERS = {"window": window_chunks, "page": page_chunks}


def header(ticker: str, form: str, report_date: object, section: str | None) -> str:
    parts = [f"{ticker} {form}"]
    if report_date:
        parts.append(f"fiscal year ended {report_date}")
    if section:
        parts.append(section)
    return ", ".join(parts)


def build_chunks(conn: psycopg.Connection, rebuild: bool = False) -> dict[str, int]:
    """Write chunks for every filing and strategy that has none (all of them with rebuild)."""
    apply_schema(conn)
    counts = dict.fromkeys(STRATEGIES, 0)
    filings = conn.execute(
        "SELECT accession, ticker, form, report_date FROM filings ORDER BY accession"
    ).fetchall()
    for accession, ticker, form, report_date in filings:
        pages = conn.execute(
            "SELECT page_index, section, label, text FROM pages WHERE accession = %s ORDER BY page_index",
            (accession,),
        ).fetchall()
        for strategy in STRATEGIES:
            existing = conn.execute(
                "SELECT count(*) FROM chunks WHERE accession = %s AND strategy = %s", (accession, strategy)
            ).fetchone()
            if existing and existing[0] and not rebuild:
                continue
            rows = []
            for page_index, section, label, text in pages:
                head = header(ticker, form, report_date, section)
                for ordinal, chunk in enumerate(CHUNKERS[strategy](text)):
                    rows.append(
                        (strategy, accession, page_index, ordinal, ticker, section, label, head, chunk)
                    )
            with conn.transaction():
                conn.execute(
                    "DELETE FROM chunks WHERE accession = %s AND strategy = %s", (accession, strategy)
                )
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO chunks (strategy, accession, page_index, ordinal, ticker,"
                        " section, label, header, text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        rows,
                    )
            counts[strategy] += len(rows)
    return counts


def embedding_dimension(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        " AND NOT attisdropped"
    ).fetchone()
    return None if row is None else int(row[0])


class EmbeddingMismatch(RuntimeError):
    pass


def ensure_embedding_column(conn: psycopg.Connection, dim: int, model: str) -> None:
    current = embedding_dimension(conn)
    if current is None:
        with conn.transaction():
            conn.execute(f"ALTER TABLE chunks ADD COLUMN embedding vector({int(dim)})")
        return
    other = conn.execute(
        "SELECT count(*) FROM chunks WHERE embedding_model IS NOT NULL AND embedding_model <> %s", (model,)
    ).fetchone()
    if current != dim or (other and other[0]):
        raise EmbeddingMismatch(
            f"chunks were embedded with another model or dimension ({current}); "
            "run `python -m app.chunks embed --reset` to start again"
        )


def to_vector(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def embed_chunks(conn: psycopg.Connection, embedder: Embedder, batch: int = 32, reset: bool = False) -> int:
    """Embed chunks without an embedding, committing each batch. Returns how many were embedded."""
    apply_schema(conn)
    if not reset:
        other = conn.execute(
            "SELECT count(*) FROM chunks WHERE embedding_model IS NOT NULL AND embedding_model <> %s",
            (embedder.model,),
        ).fetchone()
        if other and other[0]:
            raise EmbeddingMismatch(
                f"{other[0]} chunks were embedded with another model; "
                "run `python -m app.chunks embed --reset` to start again"
            )
    if reset:
        with conn.transaction():
            conn.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
            conn.execute("UPDATE chunks SET embedding_model = NULL")
    has_column = embedding_dimension(conn) is not None
    where = "embedding IS NULL" if has_column else "TRUE"
    todo = conn.execute(f"SELECT id, header, text FROM chunks WHERE {where} ORDER BY id").fetchall()
    done = 0
    for start in range(0, len(todo), batch):
        rows = todo[start : start + batch]
        vectors = embedder.embed([f"{head}\n{text}" for _, head, text in rows])
        ensure_embedding_column(conn, len(vectors[0]), embedder.model)
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE chunks SET embedding = %s::vector, embedding_model = %s WHERE id = %s",
                    [(to_vector(v), embedder.model, row[0]) for v, row in zip(vectors, rows, strict=True)],
                )
        done += len(rows)
        log.info("embedded chunks=%d of %d", done, len(todo))
    return done


def main(argv: list[str] | None = None) -> int:
    from app.llm import ModelNotConfigured, get_embedder
    from app.store import connect

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "chunk"
    started = time.monotonic()
    with connect() as conn:
        if command == "chunk":
            counts = build_chunks(conn, rebuild="--rebuild" in args)
            print(
                " ".join(f"{k}={v}" for k, v in counts.items()), f"seconds={time.monotonic() - started:.1f}"
            )
        elif command == "embed":
            try:
                embedder = get_embedder()
            except ModelNotConfigured as exc:
                print(exc, file=sys.stderr)
                return 2
            try:
                n = embed_chunks(conn, embedder, reset="--reset" in args)
            except EmbeddingMismatch as exc:
                print(exc, file=sys.stderr)
                return 2
            u = embedder.usage
            print(
                f"embedded={n} requests={u.requests} tokens={u.tokens} "
                f"seconds={time.monotonic() - started:.1f}"
            )
        else:
            print("usage: python -m app.chunks [chunk [--rebuild] | embed [--reset]]")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
