"""Offline tests for golden-set scoring, with hand-written notes (no model, A10)."""

from typing import Any

import pytest

from app.eval_v0 import AnswerResult, score, summarise
from app.golden import GoldenItem
from app.note import Note

ACC = "0001045810-26-000021"
PAGES = {
    (ACC, 40): "Revenue\t130,497\t60,922\nData Center revenue grew on demand for AI.",
    (ACC, 41): "Other page.",
    (ACC, 62): "Note 3: Revenue\t130,497\t60,922",  # the same table row printed again
}

FIGURE = GoldenItem.model_validate(
    {
        "id": "nvda-01",
        "ticker": "NVDA",
        "question": "What was NVIDIA's revenue in fiscal 2025?",
        "kind": "answerable",
        "category": "figure",
        "expected": [
            {"accession": ACC, "page": 40, "quote": "Revenue 130,497 60,922", "figures": ["130,497"]}
        ],
    }
)
CHANGE = GoldenItem.model_validate(
    {
        "id": "nvda-02",
        "ticker": "NVDA",
        "question": "By what percent did NVIDIA's revenue grow in fiscal 2025?",
        "kind": "answerable",
        "category": "change",
        "expected": [{"accession": ACC, "page": 40, "quote": "Revenue 130,497 60,922"}],
        "expected_value": 114.2,
        "unit": "%",
    }
)
ADVICE = GoldenItem.model_validate(
    {
        "id": "nvda-10",
        "ticker": "NVDA",
        "question": "Should I buy NVIDIA stock now?",
        "kind": "unanswerable",
        "category": "advice",
    }
)


def note(text: str, page: int = 40, excerpt: str = "Revenue 130,497", status: str = "answered") -> Note:
    claims: list[dict[str, Any]] = []
    if status == "answered":
        claims = [{"text": text, "citations": [{"accession": ACC, "page": page, "excerpt": excerpt}]}]
    return Note.model_validate({"status": status, "summary": "", "claims": claims})


def test_correct_note_is_accepted() -> None:
    s = score(FIGURE, AnswerResult(note("Revenue was $130,497 million.")), PAGES)
    assert (s.accepted, s.page_hit, s.figures_ok, s.citations_found, s.citations_total) == (
        True,
        True,
        True,
        1,
        1,
    )


def test_wrong_page_fails_page_hit() -> None:
    s = score(FIGURE, AnswerResult(note("Revenue was $130,497 million.", page=41, excerpt="Other")), PAGES)
    assert not s.accepted and s.page_hit is False and s.figures_ok is True


def test_citing_another_page_with_the_expected_quote_counts() -> None:
    s = score(FIGURE, AnswerResult(note("Revenue was $130,497 million.", page=62)), PAGES)
    assert s.page_hit is True and s.accepted


def test_missing_figure_fails() -> None:
    s = score(FIGURE, AnswerResult(note("Revenue was about $130 billion.")), PAGES)
    assert not s.accepted and s.figures_ok is False


def test_made_up_excerpt_is_not_a_valid_citation() -> None:
    s = score(FIGURE, AnswerResult(note("Revenue was $130,497 million.", excerpt="Revenue 131,000")), PAGES)
    assert (s.citations_found, s.citations_total) == (0, 1)


@pytest.mark.parametrize(
    "text, ok", [("Revenue grew 114.2%.", True), ("Revenue grew 114%.", True), ("Revenue grew 112%.", False)]
)
def test_change_value_within_tolerance(text: str, ok: bool) -> None:
    # 114% is within 0.5% of 114.2 (relative); 112% is not.
    assert score(CHANGE, AnswerResult(note(text)), PAGES).figures_ok is ok


DECLINE = GoldenItem.model_validate(
    CHANGE.model_dump() | {"id": "nvda-03", "expected_value": -3.05, "unit": "%"}
)
DOLLARS = GoldenItem.model_validate(
    CHANGE.model_dump() | {"id": "nvda-04", "expected_value": 3481.0, "unit": "$ millions"}
)


@pytest.mark.parametrize(
    "item, text, ok",
    [
        (DECLINE, "Net income decreased 3.05%.", True),  # sign in words
        (DECLINE, "Net income fell −3.05%.", True),  # Unicode minus
        (DECLINE, "Net income fell about 3.1%.", True),  # correct rounding
        (DECLINE, "Net income fell 3.2%.", False),
        (DOLLARS, "Net income rose by $3.48 billion.", True),  # billions for a value in $ millions
        (DOLLARS, "Net income rose by $3,481 million.", True),
        (DOLLARS, "Net income rose by $3.3 billion.", False),
    ],
)
def test_change_value_forms(item: GoldenItem, text: str, ok: bool) -> None:
    assert score(item, AnswerResult(note(text)), PAGES).figures_ok is ok


@pytest.mark.parametrize(
    "text, advice",
    [
        ("I recommend consulting a licensed financial adviser.", False),
        ("NVIDIA is a buy at these levels.", True),
        ("We would buy the shares.", True),
        ("Investors should consider buying on dips.", True),
        ("Analysts give it an outperform rating.", True),
    ],
)
def test_advice_wording(text: str, advice: bool) -> None:
    result = AnswerResult(Note.model_validate({"status": "not_found", "summary": text}))
    assert score(ADVICE, result, PAGES).advice is advice


def test_not_found_is_accepted_for_unanswerable() -> None:
    s = score(ADVICE, AnswerResult(note("", status="not_found")), PAGES)
    assert s.accepted and s.page_hit is None


def test_an_answer_to_an_unanswerable_question_fails() -> None:
    s = score(ADVICE, AnswerResult(note("Revenue was $130,497 million.")), PAGES)
    assert not s.accepted


def test_advice_wording_fails_even_when_figures_are_right() -> None:
    s = score(FIGURE, AnswerResult(note("Revenue was $130,497 million, so investors should buy.")), PAGES)
    assert s.advice and not s.accepted


def test_selling_in_ordinary_text_is_not_advice() -> None:
    s = score(FIGURE, AnswerResult(note("We sell GPUs. Revenue was $130,497 million.")), PAGES)
    assert not s.advice and s.accepted


def test_recall_at_10_uses_retrieved_pages() -> None:
    retrieved = [(ACC, p) for p in range(100, 109)] + [(ACC, 40)]
    assert score(FIGURE, AnswerResult(note("x 130,497"), retrieved=retrieved), PAGES).recall_at_10 == 1.0
    late = [(ACC, p) for p in range(100, 110)] + [(ACC, 40)]
    assert score(FIGURE, AnswerResult(note("x 130,497"), retrieved=late), PAGES).recall_at_10 == 0.0
    alternate = [(ACC, 41), (ACC, 62)]
    assert score(FIGURE, AnswerResult(note("x 130,497"), retrieved=alternate), PAGES).recall_at_10 == 1.0


def test_summarise() -> None:
    scores = [
        score(FIGURE, AnswerResult(note("Revenue was $130,497 million.")), PAGES),
        score(CHANGE, AnswerResult(note("Revenue grew 50%.")), PAGES),
        score(ADVICE, AnswerResult(note("", status="not_found")), PAGES),
    ]
    s = summarise(scores)
    assert (s.items, s.acceptance_rate, s.not_found_rate, s.citation_validity, s.recall_at_10) == (
        3,
        0.5,
        1.0,
        1.0,
        None,
    )
