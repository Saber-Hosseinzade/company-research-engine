"""Database tests for schema, ingest and the page store (`make test-db`)."""

from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.extract import extract_pages
from app.ingest import apply_schema, ingest
from app.pages import Filing
from app.store import DbSources, db_page_store, page_view
from app.validate import FileSources

FIXTURE = Path(__file__).parents[1] / "tests" / "fixtures" / "mini_10k.html"
ORIGINAL = "0000002488-26-000018"
AMENDMENT = "0000002488-26-000021"


def filing(form: str, accession: str) -> Filing:
    html = (
        FIXTURE.read_text(encoding="utf-8")
        if form == "10-K"
        else "<html><body><p>(Amendment No. 1)</p><p>Corrected widgets sentence.</p></body></html>"
    )
    meta: dict[str, Any] = {
        "ticker": "AMD",
        "cik": 2488,
        "form": form,
        "filing_date": "2026-02-04",
        "report_date": "2025-12-27",
        "accession": accession,
        "primary_document": "doc.htm",
    }
    return Filing(meta=meta, extraction=extract_pages(html))


def counts(conn: psycopg.Connection) -> tuple[int, int]:
    filings = conn.execute("SELECT count(*) FROM filings").fetchone()
    pages = conn.execute("SELECT count(*) FROM pages").fetchone()
    assert filings is not None and pages is not None
    return filings[0], pages[0]


def test_schema_applies_twice(conn: psycopg.Connection) -> None:
    apply_schema(conn)
    apply_schema(conn)
    assert counts(conn) == (0, 0)


def test_ingest_is_idempotent(conn: psycopg.Connection) -> None:
    filings = [filing("10-K/A", AMENDMENT), filing("10-K", ORIGINAL)]  # amendment first on purpose
    assert ingest(conn, filings) == (2, 7)
    ingest(conn, filings)
    assert counts(conn) == (2, 7)


def test_amendment_row(conn: psycopg.Connection) -> None:
    ingest(conn, [filing("10-K", ORIGINAL), filing("10-K/A", AMENDMENT)])
    row = conn.execute("SELECT form, amendment_no, amends FROM filings WHERE accession = %s", (AMENDMENT,))
    assert row.fetchone() == ("10-K/A", 1, ORIGINAL)


def test_amends_must_reference_a_stored_filing(conn: psycopg.Connection) -> None:
    apply_schema(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO filings (accession, ticker, cik, form, filing_date, primary_document, amends,"
            " page_count) VALUES ('x', 'AMD', 2488, '10-K/A', '2026-02-04', 'a.htm', 'missing', 1)"
        )


def test_page_store_and_full_text(conn: psycopg.Connection) -> None:
    f = filing("10-K", ORIGINAL)
    ingest(conn, [f])
    store = db_page_store(conn)
    assert store == {(ORIGINAL, p.index): p.text for p in f.extraction.pages}
    hits = conn.execute(
        "SELECT page_index FROM pages WHERE tsv @@ plainto_tsquery('english', 'widget')"
    ).fetchall()
    assert hits == [(3,)]  # stemming: "widget" finds "widgets"


def test_db_sources_match_file_sources(conn: psycopg.Connection) -> None:
    filings = [filing("10-K", ORIGINAL), filing("10-K/A", AMENDMENT)]
    ingest(conn, filings)
    db, files = DbSources(conn), FileSources(filings)
    for accession in (ORIGINAL, AMENDMENT, "missing"):
        assert db.filing(accession) == files.filing(accession)
    for key in [(ORIGINAL, 1), (ORIGINAL, 5), (AMENDMENT, 1), (ORIGINAL, 99)]:
        assert db.page_text(*key) == files.page_text(*key)


def test_page_view_joins_filing_and_builds_edgar_link(conn: psycopg.Connection) -> None:
    f = filing("10-K", ORIGINAL)
    ingest(conn, [f])
    first = f.extraction.pages[0]
    view = page_view(conn, ORIGINAL, first.index)
    assert view is not None
    assert (view.ticker, view.form, view.filing_date, view.text) == ("AMD", "10-K", "2026-02-04", first.text)
    assert view.edgar_url == "https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/doc.htm"
    assert page_view(conn, ORIGINAL, 999) is None
