"""Database tests (`make test-db`): run against the local Postgres from compose.yaml.

Each test works in its own throwaway schema, dropped afterwards. An unreachable database fails
the run with a message; it never skips.
"""

import uuid
from collections.abc import Iterator

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
    connection.autocommit = True
    connection.execute(f'CREATE SCHEMA "{schema}"')  # generated schema name
    connection.execute(f'SET search_path TO "{schema}", public')  # public: the vector extension
    try:
        yield connection
    finally:
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        connection.close()
