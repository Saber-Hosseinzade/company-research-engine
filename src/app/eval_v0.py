"""Score research notes against the golden set. Code only (A4); no model judges the answers.

Per item:
- answerable: `page_hit` (a citation on an expected page, or on any page carrying an expected quote),
  `figures_ok` (every expected figure appears in the note; for "change" items a number within
  tolerance of `expected_value`); accepted = both, with status "answered" and no advice wording.
- unanswerable: accepted = status "not_found" and no advice wording.
- every item: how many of its excerpts are found on their cited page (spec R7).
- `recall_at_10` when the answerer reports the pages it retrieved.

M5 implements `Answerer` and adds `make eval-v0`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.citations import excerpt_on_page, figure_in_text, normalise
from app.golden import GoldenItem
from app.note import Note
from app.pages import PageStore
from app.policy import contains_advice

PageKey = tuple[str, int]  # (accession, page index)

_NUMBER_RE = re.compile(r"(\d[\d,]*)(\.\d+)?(\s*billion)?", re.IGNORECASE)


@dataclass(frozen=True)
class AnswerResult:
    note: Note
    retrieved: Sequence[PageKey] | None = None  # pages the answerer retrieved, best first


class Answerer(Protocol):
    def answer(self, ticker: str, question: str) -> AnswerResult: ...


@dataclass(frozen=True)
class ItemScore:
    id: str
    kind: str
    accepted: bool
    page_hit: bool | None  # None for unanswerable items
    figures_ok: bool | None
    citations_found: int  # excerpts found on their cited page
    citations_total: int
    recall_at_10: float | None
    advice: bool


@dataclass(frozen=True)
class Summary:
    items: int
    acceptance_rate: float  # answerable items accepted
    not_found_rate: float  # unanswerable items accepted
    citation_validity: float | None  # all excerpts across all notes
    recall_at_10: float | None
    advice_items: int


def _note_text(note: Note) -> str:
    parts = [note.summary]
    for claim in note.claims:
        parts.append(claim.text)
        parts.extend(f.value for f in claim.figures)
    return "\n".join(parts)


def _change_value_found(text: str, item: GoldenItem) -> bool:
    """True when the note states the change, as an exact or correctly rounded magnitude.

    Magnitudes only: the sign is often in words ("decreased 3.05%"). A number written with fewer
    decimals counts when it is the expected value rounded ("114%" for 114.2). "$3.48 billion" counts
    for a value in $ millions.
    """
    assert item.expected_value is not None
    target = abs(item.expected_value)
    millions = "million" in (item.unit or "").lower()
    for m in _NUMBER_RE.finditer(normalise(text)):
        decimals = len(m.group(2)) - 1 if m.group(2) else 0
        value = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        rounding = 0.5 * 10**-decimals
        if m.group(3) and millions:
            value, rounding = value * 1000, rounding * 1000
        if abs(value - target) <= max(target * item.tolerance, rounding) + 1e-9:
            return True
    return False


def _citation_counts(note: Note, pages: PageStore) -> tuple[int, int]:
    valid = total = 0
    for claim in note.claims:
        for c in claim.citations:
            total += 1
            text = pages.get((c.accession, c.page))
            if text is not None and excerpt_on_page(c.excerpt, text):
                valid += 1
    return valid, total


def expected_pages(item: GoldenItem, pages: PageStore) -> set[PageKey]:
    """The golden pages, plus any other page that carries one of the expected quotes.

    Filings repeat text (a table row in MD&A and in the notes, a sentence in risk factors and in
    MD&A), so a citation to another page with the same quote is just as right.
    """
    keys = {(r.accession, r.page) for r in item.expected}
    for ref in item.expected:
        keys |= {key for key, text in pages.items() if excerpt_on_page(ref.quote, text)}
    return keys


def score(item: GoldenItem, result: AnswerResult, pages: PageStore) -> ItemScore:
    note = result.note
    text = _note_text(note)
    advice = contains_advice(text)
    found, total = _citation_counts(note, pages)
    expected = expected_pages(item, pages)

    recall = None
    if result.retrieved is not None and item.expected:
        # Hit when any page carrying the ref's quote is in the top 10, per expected ref.
        top = set(result.retrieved[:10])
        hits = sum(
            1
            for ref in item.expected
            if any(
                excerpt_on_page(ref.quote, pages.get(k, "")) or k == (ref.accession, ref.page) for k in top
            )
        )
        recall = hits / len(item.expected)

    if item.kind == "unanswerable":
        return ItemScore(
            id=item.id,
            kind=item.kind,
            accepted=note.status == "not_found" and not advice,
            page_hit=None,
            figures_ok=None,
            citations_found=found,
            citations_total=total,
            recall_at_10=recall,
            advice=advice,
        )

    cited = {(c.accession, c.page) for claim in note.claims for c in claim.citations}
    page_hit = bool(cited & expected)
    if item.category == "change" and item.expected_value is not None:
        figures_ok = _change_value_found(text, item)
    else:
        figures_ok = all(figure_in_text(f, text) for r in item.expected for f in r.figures)
    accepted = note.status == "answered" and page_hit and figures_ok and not advice
    return ItemScore(
        id=item.id,
        kind=item.kind,
        accepted=accepted,
        page_hit=page_hit,
        figures_ok=figures_ok,
        citations_found=found,
        citations_total=total,
        recall_at_10=recall,
        advice=advice,
    )


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def summarise(scores: Sequence[ItemScore]) -> Summary:
    answerable = [s.accepted for s in scores if s.kind == "answerable"]
    unanswerable = [s.accepted for s in scores if s.kind == "unanswerable"]
    recalls = [s.recall_at_10 for s in scores if s.recall_at_10 is not None]
    found = sum(s.citations_found for s in scores)
    total = sum(s.citations_total for s in scores)
    return Summary(
        items=len(scores),
        acceptance_rate=_rate(answerable),
        not_found_rate=_rate(unanswerable),
        citation_validity=found / total if total else None,
        recall_at_10=sum(recalls) / len(recalls) if recalls else None,
        advice_items=sum(1 for s in scores if s.advice),
    )
