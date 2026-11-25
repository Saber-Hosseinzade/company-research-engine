"""Load the fetched filings and their pages into Postgres (spec R3, R4).

Run with `make ingest` after `make fetch` and `make db-up`. Re-running replaces each filing's rows, so
it never duplicates. Row building is plain code and tested offline; writing is tested by `make test-db`.
"""

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from importlib import resources

import psycopg

from app.pages import Filing, load_filings
from app.store import Conn

log = logging.getLogger(__name__)

_AMENDMENT_RE = re.compile(r"\bAmendment\s+No\.\s*(\d+)", re.IGNORECASE)
AMENDMENT_SEARCH_PAGES = 3


@dataclass(frozen=True)
class FilingRow:
    accession: str
    ticker: str
    cik: int
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    amendment_no: int | None
    amends: str | None
    page_count: int


@dataclass(frozen=True)
class PageRow:
    accession: str
    page_index: int
    label: str | None
    section: str | None
    items_started: list[str]
    text: str


def _date(value: object) -> date | None:
    text = str(value or "")[:10]
    return date.fromisoformat(text) if text else None


def amendment_number(filing: Filing) -> int | None:
    if filing.form != "10-K/A":
        return None
    for page in filing.extraction.pages[:AMENDMENT_SEARCH_PAGES]:
        if m := _AMENDMENT_RE.search(page.text):
            return int(m.group(1))
    return None


def filing_row(filing: Filing) -> FilingRow:
    meta = filing.meta
    filing_date = _date(meta["filing_date"])
    assert filing_date is not None
    return FilingRow(
        accession=filing.accession,
        ticker=filing.ticker,
        cik=int(meta["cik"]),
        form=filing.form,
        filing_date=filing_date,
        report_date=_date(meta.get("report_date")),
        primary_document=str(meta["primary_document"]),
        amendment_no=amendment_number(filing),
        amends=None,
        page_count=len(filing.extraction.pages),
    )


def page_rows(filing: Filing) -> list[PageRow]:
    return [
        PageRow(
            accession=filing.accession,
            page_index=p.index,
            label=p.label,
            section=p.section,
            items_started=list(p.items_started),
            text=p.text,
        )
        for p in filing.extraction.pages
    ]


def link_amendments(rows: Iterable[FilingRow]) -> list[FilingRow]:
    """Point each 10-K/A at the 10-K with the same CIK and fiscal period end, when there is one."""
    rows = list(rows)
    originals = {(r.cik, r.report_date): r.accession for r in rows if r.form == "10-K" and r.report_date}
    return [
        replace(r, amends=originals.get((r.cik, r.report_date)))
        if r.form == "10-K/A" and r.report_date
        else r
        for r in rows
    ]


def schema_statements() -> list[str]:
    """schema.sql split into single statements: the RDS Data API runs one per call.

    Comments are dropped first; the file has no `;` inside a statement body (tests/test_ingest.py).
    """
    sql = resources.files("app").joinpath("schema.sql").read_text(encoding="utf-8")
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply_schema(conn: Conn) -> None:
    with conn.transaction():
        for statement in schema_statements():
            conn.execute(statement)  # trusted DDL file shipped with the package


def write_filing(conn: psycopg.Connection, row: FilingRow, pages: list[PageRow]) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM pages WHERE accession = %s", (row.accession,))
        conn.execute(
            """
            INSERT INTO filings (accession, ticker, cik, form, filing_date, report_date,
                                 primary_document, amendment_no, amends, page_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (accession) DO UPDATE SET
                ticker = EXCLUDED.ticker, cik = EXCLUDED.cik, form = EXCLUDED.form,
                filing_date = EXCLUDED.filing_date, report_date = EXCLUDED.report_date,
                primary_document = EXCLUDED.primary_document,
                amendment_no = EXCLUDED.amendment_no, amends = EXCLUDED.amends,
                page_count = EXCLUDED.page_count, ingested_at = now()
            """,
            (
                row.accession, row.ticker, row.cik, row.form, row.filing_date, row.report_date,
                row.primary_document, row.amendment_no, row.amends, row.page_count,
            ),
        )  # fmt: skip
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO pages (accession, page_index, label, section, items_started, text)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [(p.accession, p.page_index, p.label, p.section, p.items_started, p.text) for p in pages],
            )


def ingest(conn: psycopg.Connection, filings: list[Filing]) -> tuple[int, int]:
    """Write all filings, originals before amendments (the amends FK). Returns (filings, pages)."""
    apply_schema(conn)
    rows = {r.accession: r for r in link_amendments(filing_row(f) for f in filings)}
    ordered = sorted(filings, key=lambda f: f.form == "10-K/A")
    pages_written = 0
    for f in ordered:
        started = time.monotonic()
        pages = page_rows(f)
        write_filing(conn, rows[f.accession], pages)
        pages_written += len(pages)
        log.info(
            "ingested accession=%s pages=%d seconds=%.2f", f.accession, len(pages), time.monotonic() - started
        )
    return len(filings), pages_written


def main() -> int:
    from app.store import connect_pg

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    filings = load_filings()
    if not filings:
        print("No filings in data/filings. Run `make fetch` first.")
        return 1
    with connect_pg() as conn:
        n_filings, n_pages = ingest(conn, filings)
    print(f"filings={n_filings} pages={n_pages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
