"""Offline tests for the RDS Data API adapter (AgentCore spec R7, R8) on a fake `rds-data` client."""

from datetime import date, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.dataapi import DataApiConnection, parameters, rewrite
from app.ingest import schema_statements
from app.retrieve import fts_candidates, vector_candidates
from app.store import DatabaseUnavailable, DbSources, db_page_store

RESUMING = ClientError(
    {"Error": {"Code": "DatabaseResumingException", "Message": "resuming"}}, "ExecuteStatement"
)


class FakeRdsData:
    """Records each call; answers from a queue of responses or exceptions."""

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _next(self, method: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, kwargs))
        response = self.responses.pop(0) if self.responses else {"records": []}
        if isinstance(response, Exception):
            raise response
        return response

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        return self._next("execute_statement", kwargs)

    def batch_execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        return self._next("batch_execute_statement", kwargs)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def conn(client: FakeRdsData, clock: Clock | None = None) -> DataApiConnection:
    clock = clock or Clock()
    return DataApiConnection(client, "arn:cluster", "arn:secret", "app", clock=clock, sleep=clock.sleep)


def test_placeholders_become_named_and_casts_become_cast() -> None:
    sql, n = rewrite("SELECT 1 WHERE a = %s ORDER BY e <=> %s::vector, id LIMIT %s")
    assert n == 3
    assert sql == "SELECT 1 WHERE a = :p1 ORDER BY e <=> CAST(:p2 AS vector), id LIMIT :p3"
    assert rewrite("INSERT INTO t VALUES (%s::text[])")[0] == "INSERT INTO t VALUES (CAST(:p1 AS text[]))"


def test_casts_on_expressions_are_left_alone() -> None:
    sql = "SELECT plainto_tsquery('english', %s)::text, embedding::text FROM chunks"
    assert rewrite(sql)[0] == "SELECT plainto_tsquery('english', :p1)::text, embedding::text FROM chunks"


def test_parameter_types() -> None:
    _, fields = parameters("%s %s %s %s %s %s", ["a", 3, 0.5, True, None, date(2026, 2, 25)])
    assert fields == [
        {"name": "p1", "value": {"stringValue": "a"}},
        {"name": "p2", "value": {"longValue": 3}},
        {"name": "p3", "value": {"doubleValue": 0.5}},
        {"name": "p4", "value": {"booleanValue": True}},
        {"name": "p5", "value": {"isNull": True}},
        {"name": "p6", "value": {"stringValue": "2026-02-25"}, "typeHint": "DATE"},
    ]


@pytest.mark.parametrize("bad", [[["a", "b"]], [object()], [datetime(2026, 2, 25, 12, 0)]])
def test_arrays_and_unknown_types_are_refused(bad: list[object]) -> None:
    with pytest.raises(TypeError):
        parameters("%s", bad)


def test_placeholder_count_must_match() -> None:
    with pytest.raises(ValueError):
        parameters("%s %s", ["a"])


def test_dates_come_back_as_dates_and_nulls_as_none() -> None:
    client = FakeRdsData(
        {
            "columnMetadata": [
                {"typeName": "text"},
                {"typeName": "text"},
                {"typeName": "text"},
                {"typeName": "date"},
                {"typeName": "date"},
                {"typeName": "int4"},
            ],
            "records": [
                [
                    {"stringValue": "a-1"},
                    {"stringValue": "NVDA"},
                    {"stringValue": "10-K"},
                    {"stringValue": "2026-02-25"},
                    {"isNull": True},
                    {"longValue": 1},
                ]
            ],
        }
    )
    info = DbSources(conn(client)).filing("a-1")
    assert info is not None
    assert (info.filing_date, info.report_date, info.amendment_no) == (date(2026, 2, 25), None, 1)
    method, call = client.calls[0]
    assert method == "execute_statement"
    assert call["resourceArn"] == "arn:cluster"
    assert call["secretArn"] == "arn:secret"
    assert call["database"] == "app"
    assert call["includeResultMetadata"] is True
    assert call["parameters"] == [{"name": "p1", "value": {"stringValue": "a-1"}}]


def test_retrieval_sql_goes_through_unchanged_except_placeholders() -> None:
    row = [{"longValue": 7}, {"stringValue": "a-1"}, {"longValue": 3}]
    client = FakeRdsData({"records": [row]}, {"records": [row]})
    c = conn(client)
    assert vector_candidates(c, "NVDA", "window", [0.5, 1.0], "m") == [(7, "a-1", 3)]
    assert fts_candidates(c, "NVDA", "window", "revenue") == [(7, "a-1", 3)]
    vector_sql = client.calls[0][1]["sql"]
    assert "CAST(:p4 AS vector)" in vector_sql
    assert "%s" not in vector_sql
    assert client.calls[0][1]["parameters"][3] == {"name": "p4", "value": {"stringValue": "[0.5,1.0]"}}
    assert "plainto_tsquery('english', :p1)::text" in client.calls[1][1]["sql"]


def test_page_store_reads_one_filing_at_a_time() -> None:
    client = FakeRdsData(
        {"records": [[{"stringValue": "a-1"}], [{"stringValue": "a-2"}]]},
        {"records": [[{"longValue": 1}, {"stringValue": "one"}]]},
        {
            "records": [
                [{"longValue": 1}, {"stringValue": "two"}],
                [{"longValue": 2}, {"stringValue": "three"}],
            ]
        },
    )
    assert db_page_store(conn(client)) == {("a-1", 1): "one", ("a-2", 1): "two", ("a-2", 2): "three"}
    assert len(client.calls) == 3


def test_resuming_cluster_is_retried_until_it_answers() -> None:
    clock = Clock()
    client = FakeRdsData(RESUMING, RESUMING, RESUMING, {"records": [[{"longValue": 1}]]})
    assert conn(client, clock).execute("SELECT 1").fetchone() == (1,)
    assert len(client.calls) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_resuming_past_the_limit_is_database_unavailable() -> None:
    clock = Clock()
    client = FakeRdsData(*[RESUMING] * 50)
    with pytest.raises(DatabaseUnavailable):
        conn(client, clock).execute("SELECT 1")
    assert sum(clock.sleeps) <= 60.0
    assert sum(clock.sleeps) > 40.0  # it did wait for the resume, not give up early


def test_other_client_errors_are_not_retried() -> None:
    error = ClientError({"Error": {"Code": "BadRequestException", "Message": "syntax"}}, "ExecuteStatement")
    client = FakeRdsData(error)
    with pytest.raises(ClientError):
        conn(client).execute("SELECT nonsense")
    assert len(client.calls) == 1


def test_no_endpoint_is_database_unavailable() -> None:
    client = FakeRdsData(EndpointConnectionError(endpoint_url="https://rds-data"))
    with pytest.raises(DatabaseUnavailable):
        conn(client).execute("SELECT 1")


def test_batch_sends_one_request_with_parameter_sets() -> None:
    client = FakeRdsData()
    conn(client).batch("INSERT INTO t VALUES (%s, %s::vector)", [("a", "[1.0]"), ("b", "[2.0]")])
    method, call = client.calls[0]
    assert method == "batch_execute_statement"
    assert call["sql"] == "INSERT INTO t VALUES (:p1, CAST(:p2 AS vector))"
    assert [s[0]["value"] for s in call["parameterSets"]] == [{"stringValue": "a"}, {"stringValue": "b"}]


def test_schema_splits_into_single_statements() -> None:
    statements = schema_statements()
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS filings") for s in statements)
    assert any(s.startswith("CREATE EXTENSION IF NOT EXISTS vector") for s in statements)
    assert all(";" not in s and "--" not in s for s in statements)
    assert len(statements) == 7  # 3 tables, 3 indexes, 1 extension
