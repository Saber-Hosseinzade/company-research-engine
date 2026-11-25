"""Postgres connection and the page store read from the `pages` table.

`connect()` returns a local psycopg connection, or a `DataApiConnection` to Aurora when DB_CLUSTER_ARN is
set (AgentCore spec R7). Both satisfy `Conn`, the part of the psycopg interface the app's reads use.
Local-only tools that write in bulk (ingest, chunk, embed) use `connect_pg()`.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from app.config import Settings, get_settings
from app.validate import FilingInfo


class DatabaseUnavailable(RuntimeError):
    pass


class Rows(Protocol):
    def fetchone(self) -> tuple[Any, ...] | None: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class Conn(Protocol):
    def execute(self, query: str, params: Sequence[Any] = ..., /) -> Rows: ...
    def transaction(self) -> AbstractContextManager[Any]: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> Any: ...
    def __exit__(self, *exc: Any) -> Any: ...


def connect(settings: Settings | None = None) -> Conn:
    settings = settings or get_settings()
    if settings.db_cluster_arn:
        from app.dataapi import DataApiConnection, data_api_client

        if not settings.db_secret_arn:
            raise DatabaseUnavailable("DB_CLUSTER_ARN is set but DB_SECRET_ARN is not.")
        return DataApiConnection(
            data_api_client(settings.aws_region),
            settings.db_cluster_arn,
            settings.db_secret_arn,
            settings.db_name,
        )
    return connect_pg(settings)


def connect_pg(settings: Settings | None = None) -> psycopg.Connection:
    url = (settings or get_settings()).database_url
    try:
        # Autocommit: every `conn.transaction()` block is then its own committed transaction, so a run
        # that fails partway keeps what it finished (e.g. `make embed` after an HTTP 429).
        return psycopg.connect(url, connect_timeout=5, autocommit=True)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable("Cannot reach Postgres at DATABASE_URL. Run `make db-up`.") from exc


def db_page_store(conn: Conn) -> dict[tuple[str, int], str]:
    """(accession, page index) -> page text, the same mapping as app.pages.page_store.

    Read one filing at a time: each stays far below the Data API's 1 MiB response limit.
    """
    store: dict[tuple[str, int], str] = {}
    for (accession,) in conn.execute("SELECT accession FROM filings ORDER BY accession").fetchall():
        rows = conn.execute(
            "SELECT page_index, text FROM pages WHERE accession = %s ORDER BY page_index", (accession,)
        ).fetchall()
        store.update({(str(accession), int(idx)): str(text) for idx, text in rows})
    return store


@dataclass(frozen=True)
class PageView:
    """One stored page with its filing details, for the page view (R13)."""

    accession: str
    page: int
    ticker: str
    form: str
    filing_date: str
    label: str | None
    text: str
    edgar_url: str


def page_view(conn: Conn, accession: str, page: int) -> PageView | None:
    row = conn.execute(
        "SELECT f.ticker, f.form, f.filing_date, f.cik, f.primary_document, p.label, p.text"
        " FROM pages p JOIN filings f USING (accession) WHERE p.accession = %s AND p.page_index = %s",
        (accession, page),
    ).fetchone()
    if row is None:
        return None
    ticker, form, filed, cik, document, label, text = row
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
    return PageView(accession, page, ticker, form, str(filed), label, text, url)


class DbSources:
    """app.validate.Sources read from the `filings` and `pages` tables."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def page_text(self, accession: str, page: int) -> str | None:
        row = self._conn.execute(
            "SELECT text FROM pages WHERE accession = %s AND page_index = %s", (accession, page)
        ).fetchone()
        return None if row is None else str(row[0])

    def filing(self, accession: str) -> FilingInfo | None:
        row = self._conn.execute(
            "SELECT accession, ticker, form, filing_date, report_date, amendment_no"
            " FROM filings WHERE accession = %s",
            (accession,),
        ).fetchone()
        return None if row is None else FilingInfo(*row)
