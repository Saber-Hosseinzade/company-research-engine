"""Content policy shared by the validator and the eval: no investment advice (spec R11, A12)."""

import re

from app.citations import normalise

# Wording that gives investment advice (spec R11, A12). Kept to phrases, not bare words such as "sell",
# which filings use in ordinary sentences.
ADVICE_RE = re.compile(
    r"\b(?:you|investors) should (?:buy|sell|hold)\b"
    r"|\b(?:i|we) would (?:buy|sell)\b"
    r"|\brecommend (?:buying|selling|holding)\b"
    r"|\bconsider (?:buying|selling)\b"
    r"|\bis a (?:strong )?(?:buy|sell)\b"
    r"|\bprice target\b"
    r"|\b(?:buy|sell|hold|strong buy|outperform|underperform|overweight|underweight) "
    r"(?:rating|recommendation)\b"
    r"|\b(?:is|as) a good investment\b"
    r"|\brecommends? that (?:investors|you|clients) (?:buy|sell|hold)\b"
    r"|\brate (?:the stock|the shares|it|them|\w+) (?:a |an )?"
    r"(?:buy|sell|hold|outperform|underperform|overweight|underweight)\b"
    r"|\b(?:look|looks|is|are|appears?) (?:undervalued|overvalued)\b"
    r"|\b(?:accumulate|trim) (?:the )?(?:stock|shares|position)\b|;\s*accumulate\b"
    r"|\bshould consider (?:adding|buying|selling|trimming)\b",
    re.IGNORECASE,
)

DISCLAIMER = (
    "This note is analysis of public filings, not investment advice. It was drafted by a language model "
    "and checked in code against the cited pages; models can still be wrong, so read the cited pages "
    "before relying on it."
)

_INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def contains_advice(text: str) -> bool:
    """Advice wording, after NFKC, whitespace folding and removing invisible characters."""
    return ADVICE_RE.search(normalise(_INVISIBLE_RE.sub("", text))) is not None
