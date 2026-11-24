"""M0 spike report: how the page rule in app.extract holds up on the fetched filings.

Run with `make m0-report` after `make fetch`. Prints one row per filing; filings with no page breaks
come first. With --edgartools (network), each 10-K also lists edgartools' Items next to ours.
"""

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.edgar import FILINGS_DIR, keep_edgartools_in_data_dir
from app.extract import Extraction
from app.pages import load_filings

EXPECTED_10K_ITEMS = ("Item 1", "Item 1A", "Item 7", "Item 7A", "Item 8")


@dataclass(frozen=True)
class FilingReport:
    ticker: str
    form: str
    filing_date: str
    accession: str
    pages: int
    page_breaks: int
    empty_pages: int
    labelled: int
    last_number: int | None  # highest plain printed page number (not F-n or roman)
    median_chars: int
    max_chars: int
    items: tuple[str, ...]
    missing: tuple[str, ...]


def summarise(meta: dict[str, Any], extraction: Extraction) -> FilingReport:
    pages = extraction.pages
    lengths = [len(p.text) for p in pages] or [0]
    numbers = [int(p.label) for p in pages if p.label and p.label.isdigit()]
    items = tuple(dict.fromkeys(i for p in pages for i in p.items_started))
    missing = tuple(i for i in EXPECTED_10K_ITEMS if i not in items) if meta["form"] == "10-K" else ()
    return FilingReport(
        ticker=meta["ticker"],
        form=meta["form"],
        filing_date=meta["filing_date"],
        accession=meta["accession"],
        pages=len(pages),
        page_breaks=extraction.page_breaks,
        empty_pages=extraction.empty_pages,
        labelled=sum(1 for p in pages if p.label),
        last_number=max(numbers) if numbers else None,
        median_chars=int(statistics.median(lengths)),
        max_chars=max(lengths),
        items=items,
        missing=missing,
    )


def load_reports(filings_dir: Path) -> list[FilingReport]:
    reports = [summarise(f.meta, f.extraction) for f in load_filings(filings_dir)]
    return sorted(reports, key=lambda r: (r.page_breaks > 0, r.ticker, r.filing_date))


def _edgartools_items(accession: str) -> str:
    try:
        import edgar

        filing = edgar.find(accession)
        return ", ".join(filing.obj().items)  # type: ignore[union-attr]
    except Exception as exc:  # report only: show the failure, keep going
        return f"n/a ({type(exc).__name__})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edgartools", action="store_true", help="compare Items with edgartools")
    args = parser.parse_args(argv)
    reports = load_reports(FILINGS_DIR)
    if not reports:
        print("No filings in data/filings. Run `make fetch` first.", file=sys.stderr)
        return 1
    if args.edgartools:
        settings = get_settings()
        if not settings.sec_user_agent:
            print("SEC_USER_AGENT is not set (needed for --edgartools).", file=sys.stderr)
            return 2
        keep_edgartools_in_data_dir()
        import edgar

        edgar.set_identity(settings.sec_user_agent)

    header = "ticker form    filed      pages breaks empty labelled last# pages/last# median max"
    print(header)
    for r in reports:
        ratio = f"{r.pages / r.last_number:.2f}" if r.last_number else "-"
        print(
            f"{r.ticker:<6} {r.form:<7} {r.filing_date} {r.pages:>5} {r.page_breaks:>6} "
            f"{r.empty_pages:>5} {r.labelled:>8} {r.last_number or '-':>5} {ratio:>11} "
            f"{r.median_chars:>6} {r.max_chars:>6}"
        )
        print(f"    items: {', '.join(r.items) or '-'}")
        if r.missing:
            print(f"    MISSING: {', '.join(r.missing)}")
        if args.edgartools and r.form == "10-K":
            print(f"    edgartools: {_edgartools_items(r.accession)}")
    no_breaks = sum(1 for r in reports if r.page_breaks == 0)
    print(f"\nfilings={len(reports)} without_page_breaks={no_breaks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
