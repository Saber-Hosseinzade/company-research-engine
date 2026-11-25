"""Database tests (`make test-db`): run against the local Postgres from compose.yaml.

Each test works in its own throwaway schema, dropped afterwards. An unreachable database fails
the run with a message; it never skips.
"""

import re
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import psycopg
import pytest

from app.dataapi import DataApiConnection
from app.store import DatabaseUnavailable, connect_pg


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        connection = connect_pg()
    except DatabaseUnavailable as exc:
        pytest.fail(f"{exc} (make test-db needs the local database)", pytrace=False)
    schema = f"test_{uuid.uuid4().hex[:12]}"
    # No `autocommit = True` here: tests use connections exactly as app.store.connect_pg() makes them.
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
        other = connect_pg()
        other.execute(f'SET search_path TO "{schema[0]}", public')
        opened.append(other)
        return other

    yield make
    for other in opened:
        other.close()


# --- The RDS Data API on the local database (AgentCore spec R7) ------------------------------------
# A fake `rds-data` client that runs the rewritten SQL on a local psycopg connection and answers in the
# Data API's shapes, so the same queries are checked through DataApiConnection without AWS.

_NAMED = re.compile(r"(?<![:\w]):(p\d+)\b")
_DATE_OID = 1082


def _value(field: dict[str, Any]) -> object:
    value = field["value"]
    if value.get("isNull"):
        return None
    (raw,) = value.values()
    return date.fromisoformat(raw) if field.get("typeHint") == "DATE" else raw


def _field(value: object) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"stringValues": [str(v) for v in value]}}
    return {"stringValue": value.isoformat() if isinstance(value, date) else str(value)}


class PgRdsData:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.calls: list[str] = []

    @staticmethod
    def _sql(sql: str) -> str:
        assert "%s" not in sql, "placeholders must be rewritten before the Data API"
        return _NAMED.sub(r"%(\1)s", sql.replace("%", "%%"))

    def execute_statement(
        self, *, sql: str, parameters: list[dict[str, Any]], includeResultMetadata: bool, **_: Any
    ) -> dict[str, Any]:
        self.calls.append("execute_statement")
        cur = self.conn.execute(self._sql(sql), {p["name"]: _value(p) for p in parameters})
        if cur.description is None:
            return {"records": []}
        meta = [{"typeName": "date" if d.type_code == _DATE_OID else "other"} for d in cur.description]
        return {"columnMetadata": meta, "records": [[_field(v) for v in row] for row in cur.fetchall()]}

    def batch_execute_statement(
        self, *, sql: str, parameterSets: list[list[dict[str, Any]]], **_: Any
    ) -> dict[str, Any]:
        self.calls.append("batch_execute_statement")
        with self.conn.cursor() as cur:
            cur.executemany(self._sql(sql), [{p["name"]: _value(p) for p in s} for s in parameterSets])
        return {"updateResults": []}


@pytest.fixture
def data_api(conn: psycopg.Connection) -> DataApiConnection:
    """A DataApiConnection whose fake client runs on the test schema of `conn`."""
    return DataApiConnection(PgRdsData(conn), "arn:cluster", "arn:secret", "app")
