"""Split an HTML 10-K into pages: index, printed page label, 10-K Item section, and plain text.

Rule under test in M0 (spec concern 2): a page ends at a CSS page break (`page-break-before/after` or
`break-before/after` set to `always` or `page`). Hidden iXBRL facts are dropped; inline iXBRL tags are
unwrapped, keeping their text. Tables become one row per line with tab-separated cells.
"""

import re
from dataclasses import dataclass

import lxml.html
from lxml.html import HtmlElement

_BREAK_RE = re.compile(r"(?:page-)?break-(before|after)\s*:\s*(?:always|page)", re.IGNORECASE)
_HIDDEN_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_LABEL_RE = re.compile(r"^(?:page\s+)?((?:[A-Z]{1,2}-)?\d{1,3}|[ivxlc]{1,7})\.?$", re.IGNORECASE)
_PIPE_LABEL_RE = re.compile(r"\|\s*((?:[A-Z]{1,2}-)?\d{1,3})\.?$")
_ITEM_RE = re.compile(r"^item\s+(\d{1,2}[a-c]?)\s*(?:[.:\-–—]|$)", re.IGNORECASE)
_TOC_ROW_RE = re.compile(r"^(?:[A-Z]{1,2}-)?\d{1,3}$")

# A page listing this many Item headings is a table of contents, not where those sections start.
# Real pages start at most about 5 (short Part III Items share a page); a contents page lists ~20.
TOC_MIN_ITEMS = 10

_SKIP_TAGS = frozenset({"head", "script", "style", "title", "ix:header"})
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "center", "dd", "div", "dl", "dt", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section", "table", "tbody",
        "thead", "tfoot", "tr", "ul",
    }
)  # fmt: skip
_CELL_TAGS = frozenset({"td", "th"})

_NL = "\n"
_TAB = "\t"


class _PageBreak:
    pass


_PB = _PageBreak()


@dataclass(frozen=True)
class Page:
    index: int  # 1-based position among non-empty pages: the citation key
    label: str | None  # printed page number, when found
    section: str | None  # 10-K Item open at the start of the page, e.g. "Item 7"
    items_started: tuple[str, ...]  # Items whose heading is on this page
    text: str


@dataclass(frozen=True)
class Extraction:
    pages: list[Page]
    page_breaks: int
    empty_pages: int  # page breaks that enclosed no text; not counted in Page.index


def _walk(el: HtmlElement, out: list[str | _PageBreak]) -> None:
    tag = el.tag if isinstance(el.tag, str) else None
    if tag is None or tag in _SKIP_TAGS or _HIDDEN_RE.search(el.get("style", "")):
        if el.tail:
            out.append(el.tail)
        return
    sides = {m.group(1).lower() for m in _BREAK_RE.finditer(el.get("style", ""))}
    if "before" in sides:
        out.append(_PB)
    if tag in _BLOCK_TAGS:
        out.append(_NL)
    if el.text:
        out.append(el.text)
    for child in el:
        _walk(child, out)
    if tag in _CELL_TAGS:
        out.append(_TAB)
    if tag in _BLOCK_TAGS or tag == "br":
        out.append(_NL)
    if "after" in sides:
        out.append(_PB)
    if el.tail:
        out.append(el.tail)


def _clean_lines(raw: str) -> list[str]:
    lines = []
    for line in raw.split(_NL):
        cells = [_SPACE_RE.sub(" ", cell).strip() for cell in line.split(_TAB)]
        cells = [c for c in cells if c]
        if cells:
            lines.append(_TAB.join(cells))
    return lines


def _label(lines: list[str]) -> str | None:
    last = lines[-1].split(_TAB)[-1]
    if m := _LABEL_RE.match(last):
        return m.group(1)
    if m := _PIPE_LABEL_RE.search(last):
        return m.group(1)
    return None


def _item_headings(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        cells = line.split(_TAB)
        if len(cells) > 1 and _TOC_ROW_RE.match(cells[-1]):
            continue  # table-of-contents row ending in a page number
        if m := _ITEM_RE.match(" ".join(cells)):
            item = f"Item {m.group(1).upper()}"
            if item not in found:
                found.append(item)
    return [] if len(found) >= TOC_MIN_ITEMS else found


def extract_pages(html: str | bytes) -> Extraction:
    data = html.encode("utf-8") if isinstance(html, str) else html
    root = lxml.html.document_fromstring(data)
    tokens: list[str | _PageBreak] = []
    _walk(root, tokens)

    groups: list[list[str]] = [[]]
    for token in tokens:
        if isinstance(token, _PageBreak):
            groups.append([])
        else:
            groups[-1].append(token)

    pages: list[Page] = []
    empty = 0
    section: str | None = None
    for group in groups:
        lines = _clean_lines("".join(group))
        if not lines:
            empty += 1
            continue
        started = tuple(_item_headings(lines))
        pages.append(
            Page(
                index=len(pages) + 1,
                label=_label(lines),
                section=section,
                items_started=started,
                text=_NL.join(lines),
            )
        )
        if started:
            section = started[-1]
    return Extraction(pages=pages, page_breaks=len(groups) - 1, empty_pages=empty)
