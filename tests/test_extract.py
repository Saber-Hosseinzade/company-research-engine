"""Offline tests for page and section extraction (synthetic HTML only)."""

from pathlib import Path

import pytest

from app.extract import Extraction, extract_pages

FIXTURE = Path(__file__).parent / "fixtures" / "mini_10k.html"


@pytest.fixture(scope="module")
def mini() -> Extraction:
    return extract_pages(FIXTURE.read_bytes())


def test_splits_on_both_break_styles_and_drops_empty_pages(mini: Extraction) -> None:
    # cover, contents, Item 1, Item 1A, Item 7, Item 8; one empty break-enclosed page dropped
    assert [p.index for p in mini.pages] == [1, 2, 3, 4, 5, 6]
    assert mini.empty_pages == 1
    assert mini.page_breaks == 6


def test_hidden_ixbrl_text_is_dropped(mini: Extraction) -> None:
    assert all("HIDDEN FACT" not in p.text for p in mini.pages)


def test_display_none_block_is_dropped(mini: Extraction) -> None:
    assert all("HIDDEN BLOCK" not in p.text for p in mini.pages)


def test_inline_ixbrl_value_is_kept(mini: Extraction) -> None:
    assert "Revenue was $1,234 million." in mini.pages[4].text


def test_head_text_is_not_page_text(mini: Extraction) -> None:
    assert "Example Corp 10-K" not in mini.pages[0].text
    assert "margin" not in mini.pages[0].text


def test_table_rows_are_lines_with_tab_separated_cells(mini: Extraction) -> None:
    lines = mini.pages[4].text.split("\n")
    assert "Fiscal year\t2025\t2024" in lines
    assert "Revenue\t1,234\t1,000" in lines


def test_whitespace_collapsed_within_lines(mini: Extraction) -> None:
    assert "We make widgets." in mini.pages[2].text.split("\n")


def test_printed_page_labels(mini: Extraction) -> None:
    assert [p.label for p in mini.pages] == [None, "ii", "3", "4", None, "F-1"]


def test_page_label_with_trailing_period() -> None:
    assert extract_pages("<html><body><p>Text.</p><p>10.</p></body></html>").pages[0].label == "10"


def test_several_items_on_one_page_are_section_starts() -> None:
    html = "<html><body>" + "".join(f"<p>Item {n}. Title</p>" for n in (11, 12, 13, 14)) + "</body></html>"
    assert extract_pages(html).pages[0].items_started == ("Item 11", "Item 12", "Item 13", "Item 14")


def test_page_listing_ten_items_is_contents() -> None:
    html = "<html><body>" + "".join(f"<p>Item {n}.</p>" for n in range(1, 11)) + "</body></html>"
    assert extract_pages(html).pages[0].items_started == ()


def test_item_sections(mini: Extraction) -> None:
    started = [p.items_started for p in mini.pages]
    # Contents rows end in a page number: not section starts.
    assert started == [(), (), ("Item 1",), ("Item 1A",), ("Item 7",), ("Item 8",)]
    assert [p.section for p in mini.pages] == [None, None, None, "Item 1", "Item 1A", "Item 7"]


def test_no_page_breaks_gives_one_page() -> None:
    result = extract_pages("<html><body><p>Only text.</p></body></html>")
    assert result.page_breaks == 0
    assert [p.text for p in result.pages] == ["Only text."]


def test_item_heading_variants() -> None:
    html = (
        "<html><body><p>Item 7A: Quantitative</p><p>ITEM 9B.</p>"
        "<p>Itemized costs are listed.</p></body></html>"
    )
    assert extract_pages(html).pages[0].items_started == ("Item 7A", "Item 9B")
