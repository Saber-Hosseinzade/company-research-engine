"""Offline tests for the golden set: its schema, the committed file, and the page check."""

import re
from collections import Counter
from typing import Any

import pytest
from pydantic import ValidationError

from app.golden import GoldenItem, check_golden, load_golden

ACC = "0001045810-26-000021"


def item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "nvda-01",
        "ticker": "NVDA",
        "question": "What was NVIDIA's revenue in fiscal 2025?",
        "kind": "answerable",
        "category": "figure",
        "expected": [
            {"accession": ACC, "page": 40, "quote": "Revenue 130,497 60,922", "figures": ["130,497"]}
        ],
    }
    return base | overrides


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected": []},  # answerable needs pages
        {"kind": "unanswerable"},  # figure is not an unanswerable category
        {"kind": "unanswerable", "category": "advice"},  # unanswerable has no pages
        {"category": "change"},  # change needs expected_value
        {"ticker": "AAPL"},
        {"id": "NVDA_1"},
        {"surprise": 1},  # extra fields rejected
        {"expected": [{"accession": "123", "page": 40, "quote": "Revenue 130,497"}]},
    ],
)
def test_schema_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GoldenItem.model_validate(item(**overrides))


def test_check_golden_on_synthetic_pages() -> None:
    pages = {(ACC, 40): "Revenue\t130,497\t60,922"}
    good = GoldenItem.model_validate(item())
    assert check_golden([good], pages) == []
    bad = GoldenItem.model_validate(
        item(
            id="nvda-02",
            expected=[
                {"accession": ACC, "page": 40, "quote": "Revenue 130,497 60,922", "figures": ["130,479"]}
            ],
        )
    )
    missing = GoldenItem.model_validate(
        item(id="nvda-03", expected=[{"accession": ACC, "page": 41, "quote": "Revenue 130,497"}])
    )
    problems = check_golden([good, bad, missing, good], pages)
    assert problems == [
        "nvda-02: figure '130,479' not on 0001045810-26-000021 p40",
        "nvda-03: no page 41 in 0001045810-26-000021",
        "nvda-01: duplicate id",
    ]


def test_committed_golden_set_shape() -> None:
    items = load_golden()
    assert len({i.id for i in items}) == len(items)
    answerable = Counter(i.ticker for i in items if i.kind == "answerable")
    unanswerable = Counter(i.ticker for i in items if i.kind == "unanswerable")
    assert answerable == {t: 8 for t in ("NVDA", "AMD", "META", "GOOG", "AMZN")}
    assert unanswerable == {t: 2 for t in ("NVDA", "AMD", "META", "GOOG", "AMZN")}
    for i in items:
        assert i.id.startswith(i.ticker.lower() + "-")
        assert re.fullmatch(r"\d{10}-\d{2}-\d{6}", i.expected[0].accession) if i.expected else True
    assert {i.category for i in items} >= {
        "figure",
        "change",
        "narrative",
        "amendment",
        "out_of_scope",
        "advice",
    }
