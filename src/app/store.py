"""Postgres connection and the page store read from the `pages` table."""

import psycopg

from app.config import Settings, get_settings
from app.validate import FilingInfo


class DatabaseUnavailable(RuntimeError):
    pass


def connect(settings: Settings | None = None) -> psycopg.Connection:
    url = (settings or get_settings()).database_url
    try:
        # Autocommit: every `conn.transaction()` block is then its own committed transaction, so a run
        # that fails partway keeps what it finished (e.g. `make embed` after an HTTP 429).
        return psycopg.connect(url, connect_timeout=5, autocommit=True)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable("Cannot reach Postgres at DATABASE_URL. Run `make db-up`.") from exc


def db_page_store(conn: psycopg.Connection) -> dict[tuple[str, int], str]:
    """(accession, page index) -> page text, the same mapping as app.pages.page_store."""
    rows = conn.execute("SELECT accession, page_index, text FROM pages").fetchall()
    return {(acc, idx): text for acc, idx, text in rows}


class DbSources:
    """app.validate.Sources read from the `filings` and `pages` tables."""

    def __init__(self, conn: psycopg.Connection) -> None:
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
