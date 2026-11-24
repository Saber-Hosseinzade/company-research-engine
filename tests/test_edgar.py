"""Offline tests for the EDGAR fetch: a fake filing source, no network."""

import json
import os
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pytest

from app import edgar
from app.config import Settings
from app.edgar import FilingRecord, fetch


def record(form: str, filed: date, accession: str, ticker: str = "NVDA") -> FilingRecord:
    return FilingRecord(
        ticker=ticker,
        cik=1045810,
        form=form,
        filing_date=filed,
        report_date="2025-01-26",
        accession=accession,
        primary_document="doc.htm",
    )


class FakeSource:
    def __init__(self, records: list[FilingRecord]) -> None:
        self.records = records
        self.downloads: list[str] = []

    def list_filings(self, ticker: str) -> Iterable[FilingRecord]:
        return [r for r in self.records if r.ticker == ticker]

    def download_html(self, record: FilingRecord) -> str:
        self.downloads.append(record.accession)
        return f"<html>{record.accession}</html>"


RECORDS = [
    record("10-K", date(2025, 2, 26), "0001-25-000001"),
    record("10-K/A", date(2024, 1, 1), "0001-24-000002"),
    record("10-K", date(2023, 12, 31), "0001-23-000003"),
    record("10-Q", date(2025, 5, 28), "0001-25-000004"),
]


def test_keeps_10k_and_10ka_filed_from_2024(tmp_path: Path) -> None:
    source = FakeSource(RECORDS)
    result = fetch(source, tmp_path, tickers=["NVDA"])
    assert source.downloads == ["0001-25-000001", "0001-24-000002"]
    assert (result.saved, result.skipped, result.out_of_scope) == (2, 0, 2)


def test_writes_document_and_meta(tmp_path: Path) -> None:
    fetch(FakeSource(RECORDS[:1]), tmp_path, tickers=["NVDA"])
    folder = tmp_path / "1045810" / "0001-25-000001"
    assert (folder / "doc.htm").read_text() == "<html>0001-25-000001</html>"
    meta = json.loads((folder / "meta.json").read_text())
    assert meta == {
        "ticker": "NVDA",
        "cik": 1045810,
        "form": "10-K",
        "filing_date": "2025-02-26",
        "report_date": "2025-01-26",
        "accession": "0001-25-000001",
        "primary_document": "doc.htm",
    }


def test_skips_filings_already_on_disk(tmp_path: Path) -> None:
    fetch(FakeSource(RECORDS), tmp_path, tickers=["NVDA"])
    source = FakeSource(RECORDS)
    result = fetch(source, tmp_path, tickers=["NVDA"])
    assert source.downloads == []
    assert result.skipped == 2


def test_rejects_tickers_out_of_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="AAPL"):
        fetch(FakeSource([]), tmp_path, tickers=["NVDA", "AAPL"])


@pytest.mark.parametrize("name", ["../evil.htm", "sub/doc.htm"])
def test_rejects_document_names_with_paths(tmp_path: Path, name: str) -> None:
    bad = FilingRecord(
        **{**record("10-K", date(2025, 2, 26), "0001-25-000001").__dict__, "primary_document": name}
    )
    with pytest.raises(ValueError, match="unsafe document name"):
        fetch(FakeSource([bad]), tmp_path, tickers=["NVDA"])
    assert not any(tmp_path.rglob("*.htm"))


def test_edgartools_storage_points_into_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGAR_LOCAL_DATA_DIR", "/elsewhere")
    monkeypatch.setenv("EDGAR_CACHE_DIR", "/elsewhere")
    edgar.keep_edgartools_in_data_dir()
    data = edgar.DATA_DIR.resolve()
    for name in ("EDGAR_LOCAL_DATA_DIR", "EDGAR_CACHE_DIR"):
        assert Path(os.environ[name]).is_relative_to(data)


def test_source_returning_other_tickers_is_filtered(tmp_path: Path) -> None:
    class LeakySource(FakeSource):
        def list_filings(self, ticker: str) -> Iterable[FilingRecord]:
            return [record("10-K", date(2025, 2, 1), "0009-25-000009", ticker="AAPL")]

    source = LeakySource([])
    result = fetch(source, tmp_path, tickers=["NVDA"])
    assert source.downloads == []
    assert result.out_of_scope == 1


def test_main_stops_without_user_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(edgar, "get_settings", lambda: Settings(_env_file=None))
    assert edgar.main() == 2
    assert "SEC_USER_AGENT is not set" in capsys.readouterr().err
