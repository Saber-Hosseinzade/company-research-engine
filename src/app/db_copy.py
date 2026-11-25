"""`make db-copy`: copy filings, pages and window chunks (with embeddings) from the local database to
Aurora through the RDS Data API (AgentCore spec R9). No re-embedding: the deployed agent searches the
vectors that were measured.

The target's schema is applied first; a non-empty target is refused unless `--replace` is given. Chunk
ids are kept, because search ties break on id. Rows go in batches under the Data API's 4 MiB request cap.
Ends by checking that the row counts match.
"""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Protocol

import psycopg

from app.chunks import embedding_dimension, ensure_embedding_column
from app.ingest import apply_schema
from app.retrieve import AGENT_STRATEGY
from app.store import Conn

BATCH_ROWS = 200
BATCH_CHARS = 2_500_000  # text per request; the cap is 4 MiB including JSON framing


class CopyRefused(RuntimeError):
    pass


class Target(Conn, Protocol):
    def batch(self, query: str, rows: Sequence[Sequence[object]]) -> None: ...


FILINGS = (
    "SELECT accession, ticker, cik, form, filing_date, report_date, primary_document, amendment_no, amends,"
    " page_count FROM filings ORDER BY amends IS NOT NULL, accession"  # originals before amendments (FK)
)
INSERT_FILING = (
    "INSERT INTO filings (accession, ticker, cik, form, filing_date, report_date, primary_document,"
    " amendment_no, amends, page_count) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
PAGES = (
    "SELECT accession, page_index, label, section, items_started::text, text FROM pages"
    " ORDER BY accession, page_index"
)
INSERT_PAGE = (
    "INSERT INTO pages (accession, page_index, label, section, items_started, text)"
    " VALUES (%s, %s, %s, %s, %s::text[], %s)"  # no array parameters in the Data API: text, cast
)
CHUNKS = (
    "SELECT id, strategy, accession, page_index, ordinal, ticker, section, label, header, text,"
    " embedding_model, embedding::text FROM chunks WHERE strategy = %s ORDER BY id"
)
INSERT_CHUNK = (
    "INSERT INTO chunks (id, strategy, accession, page_index, ordinal, ticker, section, label, header,"
    " text, embedding_model, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)"
)


def batches(rows: Iterable[Sequence[object]]) -> Iterator[list[Sequence[object]]]:
    """Groups of at most BATCH_ROWS rows and about BATCH_CHARS characters of text."""
    batch: list[Sequence[object]] = []
    chars = 0
    for row in rows:
        size = sum(len(v) for v in row if isinstance(v, str))
        if batch and (len(batch) >= BATCH_ROWS or chars + size > BATCH_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(row)
        chars += size
    if batch:
        yield batch


def counts(conn: Conn, strategy: str = AGENT_STRATEGY) -> tuple[int, int, int]:
    def one(sql: str, params: Sequence[Any] = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    return (
        one("SELECT count(*) FROM filings"),
        one("SELECT count(*) FROM pages"),
        one("SELECT count(*) FROM chunks WHERE strategy = %s", (strategy,)),
    )


def copy(
    source: psycopg.Connection, target: Target, embedding_model: str, replace: bool = False
) -> tuple[int, int, int]:
    """Copy everything the agent reads; returns the (filings, pages, chunks) counts on the target.

    `embedding_model` is the model the deployed runtime embeds queries with; the chunks must match it.
    """
    # Every check before the first write: a refused copy leaves the target as it was.
    models = source.execute(
        "SELECT DISTINCT embedding_model FROM chunks WHERE strategy = %s AND embedding_model IS NOT NULL",
        (AGENT_STRATEGY,),
    ).fetchall()
    dim = embedding_dimension(source)
    if len(models) != 1 or dim is None:
        raise CopyRefused(
            f"Expected one embedding model in the local {AGENT_STRATEGY} chunks, found {len(models)}."
        )
    if str(models[0][0]) != embedding_model:
        raise CopyRefused(
            f"The local chunks were embedded with {models[0][0]},"
            f" but EMBEDDING_MODEL_ID is {embedding_model}."
        )

    apply_schema(target)
    if counts(target)[0] or counts(target)[2]:
        if not replace:
            raise CopyRefused("The target database already holds filings. Use --replace to overwrite it.")
        target.execute("TRUNCATE filings CASCADE")
        target.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")

    ensure_embedding_column(target, dim, embedding_model)

    for batch in batches(source.execute(FILINGS).fetchall()):
        target.batch(INSERT_FILING, batch)
    for batch in batches(source.execute(PAGES).fetchall()):
        target.batch(INSERT_PAGE, batch)
    for batch in batches(source.execute(CHUNKS, (AGENT_STRATEGY,)).fetchall()):
        target.batch(INSERT_CHUNK, batch)
    target.execute("SELECT setval(pg_get_serial_sequence('chunks', 'id'), (SELECT max(id) FROM chunks))")

    expected, got = counts(source), counts(target)
    if got != expected:
        raise CopyRefused(f"Row counts differ after the copy: local {expected}, target {got}.")
    return got


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from app.config import get_settings
    from app.dataapi import DataApiConnection, data_api_client
    from app.store import connect_pg

    parser = argparse.ArgumentParser(description="Copy the filings database to Aurora (RDS Data API).")
    parser.add_argument("--replace", action="store_true", help="overwrite a target that already has data")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.embedding_model_id:
        print("Set EMBEDDING_MODEL_ID: the model the deployed runtime embeds queries with.", file=sys.stderr)
        return 2
    if not (settings.db_cluster_arn and settings.db_secret_arn):
        print("Set DB_CLUSTER_ARN and DB_SECRET_ARN (the stack outputs) to copy to Aurora.", file=sys.stderr)
        return 2
    target = DataApiConnection(
        data_api_client(settings.aws_region),
        settings.db_cluster_arn,
        settings.db_secret_arn,
        settings.db_name,
    )
    with connect_pg(settings) as source:  # DATABASE_URL: the local database
        try:
            filings, pages, chunks = copy(source, target, settings.embedding_model_id, replace=args.replace)
        except CopyRefused as exc:
            print(exc, file=sys.stderr)
            return 1
    print(f"filings={filings} pages={pages} chunks={chunks} (counts match)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
