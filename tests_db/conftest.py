"""Database tests (`make test-db`): run against the local Postgres from compose.yaml.

Each test works in its own throwaway schema, dropped afterwards. An unreachable database fails
the run with a message; it never skips.
"""

import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest

from app.store import DatabaseUnavailable, connect


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        connection = connect()
    except DatabaseUnavailable as exc:
        pytest.fail(f"{exc} (make test-db needs the local database)", pytrace=False)
    schema = f"test_{uuid.uuid4().hex[:12]}"
    # No `autocommit = True` here: tests use connections exactly as app.store.connect() makes them.
    connection.execute(f'CREATE SCHEMA "{schema}"')  # generated schema name
    connection.execute(f'SET search_path TO "{schema}", public')  # public: the vector extension
    connection.commit()
    try:
        yield connection
    finally:
        connection.rollback()
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        connection.commit()
        connection.close()


@pytest.fixture
def reopen(conn: psycopg.Connection) -> Iterator[Callable[[], psycopg.Connection]]:
    """A second, fresh connection on the same test schema: sees only committed work."""
    schema = conn.execute("SELECT current_schema()").fetchone()
    assert schema is not None
    opened: list[psycopg.Connection] = []

    def make() -> psycopg.Connection:
        other = connect()
        other.execute(f'SET search_path TO "{schema[0]}", public')
        opened.append(other)
        return other

    yield make
    for other in opened:
        other.close()
