"""Fetch the 10-K and 10-K/A filings in scope from SEC EDGAR into data/filings/.

Run with `make fetch`. edgartools handles the SEC identity, the 10 requests/second limit and paging;
the scope rule (tickers, forms, filing date) is applied here as well, so it is ours and tested.
"""

import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)

TICKERS = ("NVDA", "AMD", "META", "GOOG", "AMZN")
FORMS = frozenset({"10-K", "10-K/A"})
FIRST_FILING_DATE = date(2024, 1, 1)
DATA_DIR = Path("data")
FILINGS_DIR = DATA_DIR / "filings"


@dataclass(frozen=True)
class FilingRecord:
    ticker: str
    cik: int
    form: str
    filing_date: date
    report_date: str
    accession: str
    primary_document: str


class FilingSource(Protocol):
    def list_filings(self, ticker: str) -> Iterable[FilingRecord]: ...

    def download_html(self, record: FilingRecord) -> str: ...


@dataclass
class FetchResult:
    saved: int = 0
    skipped: int = 0
    out_of_scope: int = 0


def keep_edgartools_in_data_dir() -> None:
    """Point both edgartools storage locations into data/ (S8). Call before importing edgar."""
    os.environ["EDGAR_LOCAL_DATA_DIR"] = str((DATA_DIR / "edgar").resolve())
    os.environ["EDGAR_CACHE_DIR"] = str((DATA_DIR / "edgar_cache").resolve())


def in_scope(record: FilingRecord) -> bool:
    return record.ticker in TICKERS and record.form in FORMS and record.filing_date >= FIRST_FILING_DATE


def filing_dir(out_dir: Path, record: FilingRecord) -> Path:
    return out_dir / str(record.cik) / record.accession


def fetch(source: FilingSource, out_dir: Path, tickers: Sequence[str] = TICKERS) -> FetchResult:
    unknown = [t for t in tickers if t not in TICKERS]
    if unknown:
        raise ValueError(f"tickers not in scope: {', '.join(unknown)}")
    result = FetchResult()
    for ticker in tickers:
        for record in source.list_filings(ticker):
            if not in_scope(record):
                result.out_of_scope += 1
                continue
            if Path(record.primary_document).name != record.primary_document:
                raise ValueError(f"unsafe document name in {record.accession}")
            target = filing_dir(out_dir, record)
            meta_path = target / "meta.json"
            if meta_path.exists():
                result.skipped += 1
                continue
            started = time.monotonic()
            html = source.download_html(record)
            target.mkdir(parents=True, exist_ok=True)
            (target / record.primary_document).write_text(html, encoding="utf-8")
            meta = asdict(record) | {"filing_date": record.filing_date.isoformat()}
            # meta.json last: its presence marks a complete download.
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            result.saved += 1
            log.info(
                "saved ticker=%s accession=%s bytes=%d seconds=%.1f",
                ticker,
                record.accession,
                len(html),
                time.monotonic() - started,
            )
    return result


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


class EdgarToolsSource:
    """FilingSource backed by edgartools. Needs network; not used in tests."""

    def __init__(self, user_agent: str) -> None:
        import edgar

        edgar.set_identity(user_agent)
        self._edgar = edgar
        self._filings: dict[str, Any] = {}

    def list_filings(self, ticker: str) -> Iterable[FilingRecord]:
        company = self._edgar.Company(ticker)
        filings = company.get_filings(
            form=sorted(FORMS), filing_date=f"{FIRST_FILING_DATE.isoformat()}:", amendments=True
        )
        for filing in filings:
            record = FilingRecord(
                ticker=ticker,
                cik=int(filing.cik),
                form=str(filing.form),
                filing_date=_as_date(filing.filing_date),
                report_date=str(filing.report_date or ""),
                accession=str(filing.accession_no),
                primary_document=str(filing.primary_document),
            )
            self._filings[record.accession] = filing
            yield record

    def download_html(self, record: FilingRecord) -> str:
        html = self._filings[record.accession].html()
        if not html:
            raise RuntimeError(f"no HTML primary document for {record.accession}")
        return str(html)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if not settings.sec_user_agent:
        print(
            "SEC_USER_AGENT is not set. Add it to .env.local, e.g. "
            'SEC_USER_AGENT="company-research-engine you@example.com"',
            file=sys.stderr,
        )
        return 2
    keep_edgartools_in_data_dir()
    result = fetch(EdgarToolsSource(settings.sec_user_agent), FILINGS_DIR)
    print(f"saved={result.saved} skipped={result.skipped} out_of_scope={result.out_of_scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
