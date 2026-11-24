"""Load the fetched filings and their pages from data/filings/.

`python -m app.pages` (`make pages`) also writes every page as a text file under data/pages/, one folder
per filing, so pages can be read and searched while writing golden questions. M2 moves pages into
Postgres; until then this is the page store.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.edgar import DATA_DIR, FILINGS_DIR
from app.extract import Extraction, extract_pages

PAGES_DIR = DATA_DIR / "pages"

# (accession, page index) -> page text
PageStore = Mapping[tuple[str, int], str]


@dataclass(frozen=True)
class Filing:
    meta: dict[str, Any]
    extraction: Extraction

    @property
    def ticker(self) -> str:
        return str(self.meta["ticker"])

    @property
    def accession(self) -> str:
        return str(self.meta["accession"])

    @property
    def form(self) -> str:
        return str(self.meta["form"])

    @property
    def filing_date(self) -> str:
        return str(self.meta["filing_date"])


def load_filings(filings_dir: Path = FILINGS_DIR) -> list[Filing]:
    filings = []
    for meta_path in sorted(filings_dir.glob("*/*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        html = (meta_path.parent / meta["primary_document"]).read_bytes()
        filings.append(Filing(meta=meta, extraction=extract_pages(html)))
    return filings


def page_store(filings: Iterable[Filing]) -> dict[tuple[str, int], str]:
    return {(f.accession, p.index): p.text for f in filings for p in f.extraction.pages}


def dump_pages(filings: Iterable[Filing], out_dir: Path) -> int:
    written = 0
    for f in filings:
        folder = out_dir / f.ticker / f"{f.filing_date}_{f.form.replace('/', '')}_{f.accession}"
        folder.mkdir(parents=True, exist_ok=True)
        for p in f.extraction.pages:
            header = f"[{f.ticker} {f.form} filed {f.filing_date} accession {f.accession} page {p.index}"
            header += f" printed {p.label or '-'} section {p.section or '-'}]\n"
            (folder / f"p{p.index:03d}.txt").write_text(header + p.text + "\n", encoding="utf-8")
            written += 1
    return written


def main() -> int:
    filings = load_filings()
    if not filings:
        print("No filings in data/filings. Run `make fetch` first.")
        return 1
    print(f"filings={len(filings)} pages={dump_pages(filings, PAGES_DIR)} dir={PAGES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
