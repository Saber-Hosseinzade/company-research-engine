"""Excerpt and figure matching against page text (spec R7). Plain code, no model.

Normalisation: Unicode NFKC, curly quotes and dashes folded to ASCII, whitespace (including tabs and
non-breaking spaces) collapsed, case-insensitive. Nothing fuzzy: numbers must match digit for digit.
"""

import re
import unicodedata
from functools import lru_cache

_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
    }
)
_SPACE_RE = re.compile(r"\s+")


@lru_cache(maxsize=8192)  # page texts are normalised again for every golden item
def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_FOLD)
    return _SPACE_RE.sub(" ", folded).strip().casefold()


def excerpt_on_page(excerpt: str, page_text: str) -> bool:
    needle = normalise(excerpt)
    return bool(needle) and needle in normalise(page_text)


def figure_in_text(figure: str, text: str) -> bool:
    """True when `figure` appears in `text` as a whole number: "1,234" is not found in "11,234"."""
    needle = normalise(figure)
    if not needle:
        return False
    pattern = re.escape(needle)
    if needle[0].isdigit():
        pattern = r"(?<![\d.,])" + pattern
    if needle[-1].isdigit():
        pattern += r"(?![.,]?\d)"
    return re.search(pattern, normalise(text)) is not None
