"""The filings database on Aurora through the RDS Data API (AgentCore spec R7, R8).

`DataApiConnection` implements the small part of the psycopg connection interface the app uses
(`execute(...).fetchone()/.fetchall()`, `transaction()`, `rollback()`, context manager), so retrieval,
`DbSources` and `apply_schema` run the same SQL on both drivers.

- `%s` placeholders become named parameters `:p1..:pn`; `%s::type` becomes `CAST(:pN AS type)`, so the
  Data API never sees `::` straight after a parameter.
- Each statement commits on its own (like the local autocommit connections); `transaction()` is a no-op.
- `date` columns come back as `datetime.date`, as from psycopg.
- A paused cluster answers `DatabaseResumingException`; calls are retried for up to RESUME_LIMIT seconds,
  then `DatabaseUnavailable` (503 in the API).
"""

import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from app.store import DatabaseUnavailable

RESUME_LIMIT = 60.0  # seconds of retries while a paused cluster resumes (R8)
READ_TIMEOUT = 70  # above the Data API's 45 s statement timeout, so a slow call is never retried mid-run

_PLACEHOLDER = re.compile(r"%s(?:::([A-Za-z_][A-Za-z0-9_]*(?:\[\])?))?")


def rewrite(sql: str) -> tuple[str, int]:
    """psycopg SQL -> Data API SQL with named parameters, and the number of parameters."""
    count = 0

    def name(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        cast = match.group(1)
        return f"CAST(:p{count} AS {cast})" if cast else f":p{count}"

    return _PLACEHOLDER.sub(name, sql), count


def encode(value: object) -> dict[str, Any]:
    """One Python value as a Data API `Field`, with a type hint where Postgres needs one."""
    if value is None:
        return {"value": {"isNull": True}}
    if isinstance(value, bool):
        return {"value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"value": {"longValue": value}}
    if isinstance(value, float):
        return {"value": {"doubleValue": value}}
    if isinstance(value, datetime):
        raise TypeError("datetime parameters are not supported; send a date or text")
    if isinstance(value, date):
        return {"value": {"stringValue": value.isoformat()}, "typeHint": "DATE"}
    if isinstance(value, str):
        return {"value": {"stringValue": value}}
    # Lists included: the Data API takes no array parameters; send text and cast in SQL.
    raise TypeError(f"no Data API encoding for {type(value).__name__}")


def parameters(sql: str, params: Sequence[object]) -> tuple[str, list[dict[str, Any]]]:
    rewritten, count = rewrite(sql)
    if count != len(params):
        raise ValueError(f"SQL has {count} placeholders but {len(params)} parameters were given")
    out = []
    for i, value in enumerate(params, start=1):
        field = encode(value)
        out.append({"name": f"p{i}", **field})
    return rewritten, out


def decode(field: dict[str, Any], type_name: str) -> object:
    if field.get("isNull"):
        return None
    if "arrayValue" in field:
        array = field["arrayValue"]
        values: list[object] = next(iter(array.values()), [])
        return list(values)
    (value,) = field.values()
    if type_name == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    return value


class Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class DataApiConnection:
    def __init__(
        self,
        client: Any,
        cluster_arn: str,
        secret_arn: str,
        database: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        resume_limit: float = RESUME_LIMIT,
    ) -> None:
        self._client = client
        self._target = {"resourceArn": cluster_arn, "secretArn": secret_arn, "database": database}
        self._clock, self._sleep, self._limit = clock, sleep, resume_limit

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        from botocore.exceptions import ClientError, EndpointConnectionError

        began = self._clock()
        delay = 1.0
        while True:
            try:
                result: dict[str, Any] = getattr(self._client, method)(**self._target, **kwargs)
                return result
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "DatabaseResumingException":
                    raise
                if self._clock() - began + delay > self._limit:
                    raise DatabaseUnavailable("The filings database is still resuming.") from None
                self._sleep(delay)
                delay = min(delay * 2, 8.0)
            except EndpointConnectionError:
                raise DatabaseUnavailable("Cannot reach the RDS Data API.") from None

    def execute(self, query: str, params: Sequence[object] = ()) -> Rows:
        sql, fields = parameters(query, params)
        result = self._call("execute_statement", sql=sql, parameters=fields, includeResultMetadata=True)
        types = [c.get("typeName", "") for c in result.get("columnMetadata", [])]
        rows = [
            tuple(decode(f, types[i] if i < len(types) else "") for i, f in enumerate(record))
            for record in result.get("records", [])
        ]
        return Rows(rows)

    def batch(self, query: str, rows: Sequence[Sequence[object]]) -> None:
        """One statement for many parameter sets (`BatchExecuteStatement`, under the 4 MiB request cap)."""
        if not rows:
            return
        sql = rewrite(query)[0]
        sets = [parameters(query, row)[1] for row in rows]
        self._call("batch_execute_statement", sql=sql, parameterSets=sets)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield  # every statement commits on its own

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> DataApiConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def data_api_client(region: str) -> Any:
    import boto3
    from botocore.config import Config

    config = Config(read_timeout=READ_TIMEOUT, retries={"mode": "standard", "max_attempts": 3})
    return boto3.client("rds-data", region_name=region, config=config)
