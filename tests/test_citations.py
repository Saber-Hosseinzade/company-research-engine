"""Offline tests for excerpt and figure matching (spec R7)."""

import pytest

from app.citations import excerpt_on_page, figure_in_text, normalise

PAGE = "Revenue\t130,497\t60,922\nData Center revenue was up 142% from a year ago — a record."


def test_normalise_folds_quotes_dashes_space_and_case() -> None:
    assert normalise("  It’s  “A” – B\tC\n") == 'it\'s "a" - b c'


@pytest.mark.parametrize(
    "excerpt",
    [
        "Revenue 130,497 60,922",  # tab-separated table cells
        "up 142% from a year ago - a record",  # NBSP and em dash
        "DATA CENTER REVENUE",  # case
        "data center   revenue\nwas up",  # spacing and line breaks
    ],
)
def test_excerpt_found(excerpt: str) -> None:
    assert excerpt_on_page(excerpt, PAGE)


@pytest.mark.parametrize("excerpt", ["Revenue 130,498", "up 143%", "", "   "])
def test_excerpt_not_found(excerpt: str) -> None:
    assert not excerpt_on_page(excerpt, PAGE)


@pytest.mark.parametrize("figure", ["130,497", "60,922", "142%"])
def test_figure_found(figure: str) -> None:
    assert figure_in_text(figure, PAGE)


@pytest.mark.parametrize(
    "figure, text",
    [
        ("1,234", "total 11,234 units"),  # digit before
        ("234", "total 1,234 units"),  # part of a larger number
        ("12.5", "rate 12.55%"),  # digit after
        ("1,234", "total 1,234,000"),  # continues as a larger number
        ("130,497", PAGE.replace("130,497", "130,479")),
    ],
)
def test_figure_must_match_as_whole_number(figure: str, text: str) -> None:
    assert not figure_in_text(figure, text)


def test_figure_at_sentence_end_is_found() -> None:
    assert figure_in_text("1,234", "Revenue was $1,234.")
