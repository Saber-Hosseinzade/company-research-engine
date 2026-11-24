"""Postgres connection and the page store read from the `pages` table."""

import psycopg

from app.config import Settings, get_settings


class DatabaseUnavailable(RuntimeError):
    pass


def connect(settings: Settings | None = None) -> psycopg.Connection:
    url = (settings or get_settings()).database_url
    try:
        return psycopg.connect(url, connect_timeout=5)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable("Cannot reach Postgres at DATABASE_URL. Run `make db-up`.") from exc


def db_page_store(conn: psycopg.Connection) -> dict[tuple[str, int], str]:
    """(accession, page index) -> page text, the same mapping as app.pages.page_store."""
    rows = conn.execute("SELECT accession, page_index, text FROM pages").fetchall()
    return {(acc, idx): text for acc, idx, text in rows}
