"""Offline tests for the chunkers."""

from app.chunks import header, page_chunks, window_chunks

LINES = [f"line {i:02d} " + "x" * 40 for i in range(60)]  # 48 chars each
PAGE = "\n".join(LINES)


def test_windows_respect_limit_and_never_split_lines() -> None:
    chunks = window_chunks(PAGE, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    for c in chunks:
        assert all(line in LINES for line in c.split("\n"))


def test_windows_overlap_by_one_line_and_cover_every_line() -> None:
    chunks = window_chunks(PAGE, limit=500)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.split("\n")[-1] == b.split("\n")[0]
    covered = {line for c in chunks for line in c.split("\n")}
    assert covered == set(LINES)


def test_a_line_longer_than_the_limit_is_its_own_chunk() -> None:
    long = "y" * 900
    chunks = window_chunks(f"short\n{long}\nshort again", limit=500)
    assert long in chunks
    assert chunks[0] == "short" or chunks[0].startswith("short")


def test_blank_lines_dropped_and_empty_page_has_no_chunks() -> None:
    assert window_chunks("a\n\n  \nb", limit=500) == ["a\nb"]
    assert window_chunks("", limit=500) == [] and page_chunks("  ") == []


def test_page_chunk_is_the_page() -> None:
    assert page_chunks(PAGE) == [PAGE]


def test_header() -> None:
    assert header("NVDA", "10-K", "2026-01-25", "Item 7") == "NVDA 10-K, fiscal year ended 2026-01-25, Item 7"
    assert header("AMD", "10-K/A", None, None) == "AMD 10-K/A"


def test_no_chunk_holds_only_the_overlap_line() -> None:
    a, b, c = "a" * 500, "b" * 500, "c" * 2000
    assert window_chunks(f"{a}\n{b}\n{c}", limit=1500) == [f"{a}\n{b}", c]
