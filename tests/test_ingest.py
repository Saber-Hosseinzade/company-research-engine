"""Offline tests for building filing and page rows (no database)."""

from datetime import date
from pathlib import Path
from typing import Any

from app.extract import extract_pages
from app.ingest import amendment_number, filing_row, link_amendments, page_rows
from app.pages import Filing

FIXTURE = Path(__file__).parent / "fixtures" / "mini_10k.html"
AMENDMENT_HTML = (
    "<html><body><p>FORM 10-K/A</p><p>(Amendment No. 1)</p>"
    '<div style="page-break-before:always"><p>Item 7. Corrected sentence.</p></div></body></html>'
)


def filing(form: str = "10-K", accession: str = "0000002488-26-000018", **meta: Any) -> Filing:
    html = AMENDMENT_HTML if form == "10-K/A" else FIXTURE.read_text(encoding="utf-8")
    base = {
        "ticker": "AMD",
        "cik": 2488,
        "form": form,
        "filing_date": "2026-02-04",
        "report_date": "2025-12-27",
        "accession": accession,
        "primary_document": "doc.htm",
    }
    return Filing(meta=base | meta, extraction=extract_pages(html))


def test_filing_row_fields() -> None:
    row = filing_row(filing())
    assert (row.form, row.cik, row.filing_date, row.report_date, row.page_count) == (
        "10-K",
        2488,
        date(2026, 2, 4),
        date(2025, 12, 27),
        6,
    )
    assert row.amendment_no is None and row.amends is None


def test_missing_report_date_is_null() -> None:
    assert filing_row(filing(report_date="")).report_date is None


def test_page_rows_follow_extraction() -> None:
    f = filing()
    rows = page_rows(f)
    assert [r.page_index for r in rows] == [p.index for p in f.extraction.pages]
    assert rows[2].items_started == ["Item 1"] and rows[3].section == "Item 1"
    assert rows[1].label == "ii"


def test_amendment_number_from_cover() -> None:
    assert amendment_number(filing("10-K/A", "0000002488-26-000021")) == 1
    assert amendment_number(filing()) is None


def test_amendment_links_to_original_with_same_period() -> None:
    original = filing_row(filing())
    amendment = filing_row(filing("10-K/A", "0000002488-26-000021"))
    older = filing_row(filing(accession="0000002488-25-000012", report_date="2024-12-28"))
    linked = {r.accession: r for r in link_amendments([amendment, original, older])}
    assert linked["0000002488-26-000021"].amends == "0000002488-26-000018"
    assert linked["0000002488-26-000018"].amends is None


def test_amendment_without_original_stays_unlinked() -> None:
    amendment = filing_row(filing("10-K/A", "0000002488-26-000021"))
    assert link_amendments([amendment])[0].amends is None


def test_amendment_without_report_date_is_not_linked() -> None:
    original = filing_row(filing(report_date=""))
    amendment = filing_row(filing("10-K/A", "0000002488-26-000021", report_date=""))
    assert link_amendments([original, amendment])[1].amends is None
