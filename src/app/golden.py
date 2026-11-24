"""Golden set for v0: questions over the filings in scope, with the pages and figures that answer them.

`make golden-check` verifies against the fetched pages that every expected quote and figure is on its
expected page. It proves the golden set is grounded, not that its questions are fair: a person reviews.
"""

import sys
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.citations import excerpt_on_page, figure_in_text
from app.edgar import TICKERS
from app.pages import PageStore, load_filings, page_store

GOLDEN_PATH = Path("golden/v0.jsonl")

Ticker = Literal["NVDA", "AMD", "META", "GOOG", "AMZN"]
Kind = Literal["answerable", "unanswerable"]
Category = Literal["figure", "change", "narrative", "amendment", "out_of_scope", "advice"]
UNANSWERABLE_CATEGORIES = frozenset({"out_of_scope", "advice"})

assert set(TICKERS) == set(Ticker.__args__)  # type: ignore[attr-defined]


class GoldenRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    page: int = Field(ge=1)
    quote: str = Field(min_length=10, max_length=400)
    figures: list[str] = []


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z]+-\d{2}$")
    ticker: Ticker
    question: str = Field(min_length=10, max_length=1000)
    kind: Kind
    category: Category
    expected: list[GoldenRef] = []
    expected_value: float | None = None  # for "change": the computed answer
    unit: str | None = None
    tolerance: float = Field(default=0.005, gt=0, lt=1)  # relative, for expected_value
    note: str | None = None  # why it is answerable or not; for the reviewer

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        unanswerable = self.category in UNANSWERABLE_CATEGORIES
        if unanswerable != (self.kind == "unanswerable"):
            raise ValueError(f"{self.id}: category {self.category} does not fit kind {self.kind}")
        if self.kind == "answerable" and not self.expected:
            raise ValueError(f"{self.id}: an answerable item needs expected pages")
        if self.kind == "unanswerable" and self.expected:
            raise ValueError(f"{self.id}: an unanswerable item has no expected pages")
        if self.category == "change" and self.expected_value is None:
            raise ValueError(f"{self.id}: a change item needs expected_value")
        return self


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenItem]:
    items = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                items.append(GoldenItem.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: {exc}") from exc
    return items


def check_golden(items: list[GoldenItem], pages: PageStore) -> list[str]:
    """Problems found, one line each; empty when every quote and figure is on its page."""
    problems = []
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            problems.append(f"{item.id}: duplicate id")
        seen.add(item.id)
        for ref in item.expected:
            text = pages.get((ref.accession, ref.page))
            if text is None:
                problems.append(f"{item.id}: no page {ref.page} in {ref.accession}")
                continue
            if not excerpt_on_page(ref.quote, text):
                problems.append(f"{item.id}: quote not on {ref.accession} p{ref.page}: {ref.quote[:60]!r}")
            for fig in ref.figures:
                if not figure_in_text(fig, text):
                    problems.append(f"{item.id}: figure {fig!r} not on {ref.accession} p{ref.page}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else GOLDEN_PATH
    items = load_golden(path)
    problems = check_golden(items, page_store(load_filings()))
    for line in problems:
        print(line)
    answerable = sum(1 for i in items if i.kind == "answerable")
    print(
        f"items={len(items)} answerable={answerable} unanswerable={len(items) - answerable} "
        f"problems={len(problems)}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
