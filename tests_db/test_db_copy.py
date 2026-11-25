"""`make db-copy` between two local schemas (AgentCore spec R9). The target is a DataApiConnection whose
fake `rds-data` client runs on the local database, so the batched Data API path is the one tested."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from conftest import PgRdsData
from test_retrieval import FakeEmbedder, filing

from app.chunks import build_chunks, embed_chunks
from app.dataapi import DataApiConnection
from app.db_copy import CopyRefused, copy, counts
from app.ingest import ingest
from app.retrieve import search
from app.store import DbSources, connect_pg, db_page_store


@pytest.fixture
def source(conn: psycopg.Connection) -> psycopg.Connection:
    ingest(conn, [filing("AMD", "0000002488-26-000018"), filing("NVDA", "0001045810-26-000021")])
    build_chunks(conn)
    embed_chunks(conn, FakeEmbedder())
    return conn


@pytest.fixture
def target() -> Iterator[DataApiConnection]:
    other = connect_pg()
    schema = f"test_{uuid.uuid4().hex[:12]}"
    other.execute(f'CREATE SCHEMA "{schema}"')  # generated schema name
    other.execute(f'SET search_path TO "{schema}", public')
    try:
        yield DataApiConnection(PgRdsData(other), "arn:cluster", "arn:secret", "app")
    finally:
        other.execute(f'DROP SCHEMA "{schema}" CASCADE')
        other.close()


def test_copies_everything_the_agent_reads(source: psycopg.Connection, target: DataApiConnection) -> None:
    assert copy(source, target, "fake") == counts(source) == (2, 12, 12)
    assert db_page_store(target) == db_page_store(source)
    accession = "0000002488-26-000018"
    assert DbSources(target).filing(accession) == DbSources(source).filing(accession)
    vector = FakeEmbedder().embed(["widgets"])[0]
    assert search(target, "AMD", "window", "widgets", vector, "fake") == search(
        source, "AMD", "window", "widgets", vector, "fake"
    )  # same ids, vectors and text, so the same ranking
    items = target.execute(
        "SELECT items_started FROM pages WHERE accession = %s AND page_index = 1", (accession,)
    )
    assert (
        items.fetchone()
        == source.execute(
            "SELECT items_started FROM pages WHERE accession = %s AND page_index = 1", (accession,)
        ).fetchone()
    )
    # The sequence continues after the copied ids.
    next_id = target.execute("SELECT nextval(pg_get_serial_sequence('chunks', 'id'))").fetchone()
    top = source.execute("SELECT max(id) FROM chunks WHERE strategy = 'window'").fetchone()
    assert next_id is not None and top is not None and next_id[0] > top[0]


def test_refuses_a_non_empty_target_unless_replace(
    source: psycopg.Connection, target: DataApiConnection
) -> None:
    copy(source, target, "fake")
    with pytest.raises(CopyRefused):
        copy(source, target, "fake")
    assert copy(source, target, "fake", replace=True) == (2, 12, 12)


def test_refuses_chunks_from_another_embedding_model(
    source: psycopg.Connection, target: DataApiConnection
) -> None:
    # The runtime would embed queries with another model and vector search would find nothing.
    with pytest.raises(CopyRefused, match="embedded with fake"):
        copy(source, target, "amazon/titan-embed-text-v2")
    assert target.execute("SELECT count(*) FROM filings").fetchone() == (0,)
