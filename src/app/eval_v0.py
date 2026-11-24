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


def recall_at_k(item: GoldenItem, retrieved: Sequence[PageKey], pages: PageStore, k: int = 10) -> float:
    """Share of the item's expected refs with a page in the top k: the golden page, or any page
    carrying the ref's quote."""
    top = set(retrieved[:k])
    hits = sum(
        1
        for ref in item.expected
        if (ref.accession, ref.page) in top or any(excerpt_on_page(ref.quote, pages.get(p, "")) for p in top)
    )
    return hits / len(item.expected) if item.expected else 0.0


# Categories where a page that prints every expected figure is as good as the golden page: 10-Ks print
# the same number in several places (MD&A, segment notes, human capital). Amendment and narrative items
# keep the strict rule, so a citation of superseded or unrelated text does not count (M5 decision).
FIGURE_PAGE_CATEGORIES = frozenset({"figure", "change"})


def _cited_pages_print_figures(item: GoldenItem, cited: set[PageKey], pages: PageStore) -> bool:
    if item.category not in FIGURE_PAGE_CATEGORIES:
        return False
    figures = [f for ref in item.expected for f in ref.figures]
    if not figures or not cited:
        return False
    text = "\n".join(pages.get(key, "") for key in cited)
    return all(figure_in_text(f, text) for f in figures)


def score(item: GoldenItem, result: AnswerResult, pages: PageStore) -> ItemScore:
    note = result.note
    text = _note_text(note)
    advice = contains_advice(text)
    found, total = _citation_counts(note, pages)
    expected = expected_pages(item, pages)

    recall = None
    if result.retrieved is not None and item.expected:
        recall = recall_at_k(item, result.retrieved, pages, k=10)

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
    page_hit = bool(cited & expected) or _cited_pages_print_figures(item, cited, pages)
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


def errored(s: ItemScore) -> ItemScore:
    """A run that ended in an error is never accepted, even for an unanswerable item."""
    from dataclasses import replace

    return replace(s, accepted=False)


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


# v0 targets, set from the M5 Haiku baseline (user decision 2026-11-24).
TARGETS = {"acceptance_rate": 0.85, "not_found_rate": 1.0, "citation_validity": 1.0, "advice_items": 0}


def target_failures(summary: Summary) -> list[str]:
    failures = []
    if summary.acceptance_rate < TARGETS["acceptance_rate"]:
        failures.append(f"acceptance {summary.acceptance_rate:.3f} < {TARGETS['acceptance_rate']}")
    if summary.not_found_rate < TARGETS["not_found_rate"]:
        failures.append(f"not-found {summary.not_found_rate:.3f} < {TARGETS['not_found_rate']}")
    if summary.citation_validity is not None and summary.citation_validity < TARGETS["citation_validity"]:
        failures.append(f"citation validity {summary.citation_validity:.3f} < {TARGETS['citation_validity']}")
    if summary.advice_items > TARGETS["advice_items"]:
        failures.append(f"advice items {summary.advice_items} > 0")
    return failures


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


# --- `make eval-v0`: run the agent on the golden set (M5) -------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import logging
    import sys
    import time
    from collections import Counter
    from datetime import UTC, datetime

    from app.agent import Agent
    from app.edgar import DATA_DIR
    from app.golden import load_golden
    from app.llm import ModelNotConfigured, ModelOutputError, get_chat, get_embedder
    from app.retrieve import DbRetriever
    from app.store import DbSources, connect, db_page_store

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Run the agent on the golden set and score it.")
    parser.add_argument("--ids", help="comma-separated golden ids (default: all)")
    parser.add_argument("--rescore", help="score a saved answers-*.json again (no model calls)")
    args = parser.parse_args(argv)

    items = load_golden()
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [i for i in items if i.id in wanted]
    if args.rescore:
        from pathlib import Path

        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        by_id = {i.id: i for i in items}
        with connect() as conn:
            pages = db_page_store(conn)
        rescored = []
        for row in saved["items"]:
            if row["id"] in by_id:
                note = Note.model_validate(row["note"])
                retrieved: list[PageKey] = (
                    [(str(a), int(p)) for a, p in note.run.retrieved] if note.run else []
                )
                rescored.append(score(by_id[row["id"]], AnswerResult(note, retrieved=retrieved), pages))
        summary = summarise(rescored)
        print(f"rescored {args.rescore} (model {saved['model']})")
        _print_summary(summary)
        return _verdict(summary)
    try:
        chat, embedder = get_chat(), get_embedder()
    except ModelNotConfigured as exc:
        print(exc, file=sys.stderr)
        return 2

    started = time.monotonic()
    scores: list[ItemScore] = []
    rows: list[dict[str, object]] = []
    dropped: Counter[str] = Counter()
    errors = 0
    with connect() as conn:
        agent = Agent(chat=chat, retriever=DbRetriever(conn, embedder), sources=DbSources(conn))
        pages = db_page_store(conn)
        for n, item in enumerate(items, start=1):
            try:
                note = agent.answer(item.ticker, item.question)
                error = None
            except ModelOutputError as exc:
                note, error = Note(status="not_found"), str(exc)
                errors += 1
            retrieved = note.run.retrieved if note.run else []
            s = score(item, AnswerResult(note, retrieved=retrieved), pages)
            if error is not None:
                s = errored(s)  # an error is never a correct "not found"
            scores.append(s)
            dropped.update(note.dropped)
            rows.append({"id": item.id, "score": s.__dict__, "error": error, "note": note.model_dump()})
            print(f"[{n}/{len(items)}] {item.id} {'ok' if s.accepted else 'no'}", flush=True)

    summary = summarise(scores)
    seconds = time.monotonic() - started
    usage = chat.usage
    print(f"\nmodel {chat.model}  items {summary.items}  errors {errors}")
    _print_summary(summary)
    print(f"dropped by reason         {dict(sorted(dropped.items()))}")
    print(
        f"chat calls {usage.calls}  prompt {usage.prompt_tokens}  completion {usage.completion_tokens}  "
        f"parse retries {usage.parse_retries}  cost ${usage.cost:.4f}  embed tokens {embedder.usage.tokens}  "
        f"seconds {seconds:.0f}"
    )
    out = DATA_DIR / "evals"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"answers-{chat.model.replace('/', '_')}-{stamp}.json"
    record = {
        "model": chat.model,
        "summary": summary.__dict__,
        "errors": errors,
        "dropped": dict(dropped),
        "usage": usage.__dict__,
        "embed_tokens": embedder.usage.tokens,
        "seconds": round(seconds, 1),
        "items": rows,
    }
    path.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    print(f"written {path}")
    return _verdict(summary)


def _print_summary(summary: Summary) -> None:
    validity = "-" if summary.citation_validity is None else f"{summary.citation_validity:.3f}"
    recall = "-" if summary.recall_at_10 is None else f"{summary.recall_at_10:.3f}"
    print(f"acceptance (answerable)   {summary.acceptance_rate:.3f}   target >= {TARGETS['acceptance_rate']}")
    print(f"not-found (unanswerable)  {summary.not_found_rate:.3f}   target {TARGETS['not_found_rate']}")
    print(f"citation validity         {validity}   target {TARGETS['citation_validity']}")
    print(f"recall@10 (retrieved)     {recall}")
    print(f"advice items              {summary.advice_items}   target 0")


def _verdict(summary: Summary) -> int:
    failures = target_failures(summary)
    print("targets: PASS" if not failures else "targets: FAIL (" + "; ".join(failures) + ")")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
