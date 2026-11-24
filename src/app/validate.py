"""Turn the model's draft into the final note, checking every citation and number in code (spec R6-R11).

A claim survives whole or is dropped with a reason:
- every citation is on a filing of the requested company, on a stored page, with its excerpt on that
  page (R6, R7, S10);
- every quoted figure appears in its citation's excerpt; computed figures are calculated here from
  quoted ones (R8);
- every placeholder names a figure, and any other number in the text appears in an excerpt (years
  exempt), so the model cannot state a number nobody checked;
- no advice wording (R11).
Each figure is labelled with its source filing and that filing's fiscal period (R9). With no surviving
claim the note is "not found" (R10, A8). The disclaimer is always attached (A12).
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.citations import excerpt_on_page, figure_in_text, normalise
from app.draft import ComputedFigure, Draft, DraftClaim, QuotedFigure
from app.note import Citation, Claim, Figure, Note
from app.pages import Filing
from app.policy import DISCLAIMER, contains_advice

_PLACEHOLDER_RE = re.compile(r"\{(f\d{1,3})\}")
# A placeholder touching a digit, a sign or another placeholder would build a number nobody checked.
_GLUED_RE = re.compile(r"[\d+\-\u2212]\{f\d|\}\{|\}[\d.,]?\d")
_NUMBER_RE = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?")
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_SCALES = "thousand|million|billion|trillion"
_AFTER_RE = re.compile(rf"\s*(%|{_SCALES})\b|\s*%", re.IGNORECASE)
_SCALE_AFTER_RE = re.compile(rf"\s*({_SCALES})\b", re.IGNORECASE)
_NUMBER_WORDS_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|hundreds of|several|a few|dozens of)"
    rf"\s+(?:hundred\s+)?(?:{_SCALES})\b",
    re.IGNORECASE,
)
# A quoted value is one printed number: optional brackets or sign, $, digits with thousands commas,
# decimals, %, and one scale word. "$60," or "fiscal 2025 and $60,922" are not values.
_QUOTED_RE = re.compile(
    rf"^(\()?(-)?\$?(\d{{1,3}}(?:,\d{{3}})+|\d+)(\.\d+)?(%)?(?:\s({_SCALES}))?(\))?$", re.IGNORECASE
)
MIN_EXCERPT_CHARS = 15


@dataclass(frozen=True)
class FilingInfo:
    accession: str
    ticker: str
    form: str
    filing_date: date
    report_date: date | None
    amendment_no: int | None


class Sources(Protocol):
    def page_text(self, accession: str, page: int) -> str | None: ...

    def filing(self, accession: str) -> FilingInfo | None: ...


class ClaimRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def period_label(info: FilingInfo) -> str:
    form = f"{info.form} (Amendment No. {info.amendment_no})" if info.amendment_no else info.form
    period = f" for fiscal year ended {info.report_date.isoformat()}" if info.report_date else ""
    return f"source: {form}{period}, filed {info.filing_date.isoformat()}"


@dataclass(frozen=True)
class Value:
    number: float
    decimals: int
    scale: str  # "", "thousand", "million", "billion" or "trillion"
    percent: bool


def parse_value(printed: str) -> Value | None:
    """A printed value: "$1,234.5" -> 1234.5; "(19,193)" -> -19193; "$130.5 billion" keeps its scale."""
    m = _QUOTED_RE.match(normalise(printed))
    if not m:
        return None
    open_b, minus, digits, frac, pct, scale, close_b = m.groups()
    if bool(open_b) != bool(close_b):
        return None
    number = float(digits.replace(",", "") + (frac or ""))
    negative = bool(open_b) or bool(minus)
    return Value(
        -number if negative else number, len(frac) - 1 if frac else 0, (scale or "").lower(), bool(pct)
    )


def _unit_key(unit: str | None) -> str:
    return " ".join((unit or "").lower().replace("$", " ").split())


def _scales_near(number: str, excerpt: str) -> set[str]:
    """Scale words printed right after `number` in the excerpt (empty when none)."""
    text = normalise(excerpt)
    found = set()
    for m in re.finditer(rf"(?<![\d.,]){re.escape(normalise(number))}(?![.,]?\d)", text):
        after = _SCALE_AFTER_RE.match(text, m.end())
        if after:
            found.add(after.group(1).lower())
    return found


# figure id -> (figure, parsed value, known scales)
Quoted = dict[str, tuple[QuotedFigure, Value, set[str]]]


def _compute(fig: ComputedFigure, quoted: Quoted) -> tuple[str, str | None]:
    if any(i not in quoted for i in fig.inputs):
        raise ClaimRejected("bad_inputs")
    inputs = [quoted[i] for i in fig.inputs]
    keys = {(_unit_key(q.unit), v.scale, v.percent) for q, v, _ in inputs}
    if len(keys) > 1:
        raise ClaimRejected("unit_mismatch")
    values = [v.number for _, v, _ in inputs]
    decimals = max(v.decimals for _, v, _ in inputs)
    unit = inputs[0][0].unit
    if fig.op in ("difference", "percent_change", "ratio") and len(values) != 2:
        raise ClaimRejected("bad_inputs")
    if fig.op == "sum":
        return f"{sum(values):,.{decimals}f}", unit
    a, b = values
    if fig.op == "difference":
        return f"{b - a:,.{decimals}f}", unit
    if fig.op == "percent_change":
        if a == 0:
            raise ClaimRejected("division_by_zero")
        return f"{(b - a) / abs(a) * 100:,.1f}%", "%"
    if b == 0:
        raise ClaimRejected("division_by_zero")
    return f"{a / b:,.2f}x", "x"


def _check_numbers(text: str, excerpts: list[str]) -> str | None:
    """Reason to reject free text whose numbers are not in the excerpts, or None when all are."""
    norm = normalise(text)
    if _NUMBER_WORDS_RE.search(norm):
        return "unsupported_number"
    for m in _NUMBER_RE.finditer(norm):
        number = m.group(0)
        after = _AFTER_RE.match(norm, m.end())
        bare_year = _YEAR_RE.fullmatch(number) and not after and not norm[: m.start()].endswith("$")
        if bare_year:
            continue
        if not any(figure_in_text(number, e) for e in excerpts):
            return "unsupported_number"
        scale = _SCALE_AFTER_RE.match(norm, m.end())
        if scale:
            printed = set().union(*(_scales_near(number, e) for e in excerpts))
            if printed and scale.group(1).lower() not in printed:
                return "scale_mismatch"
    return None


def _check_placeholder_scales(text: str, figures: dict[str, set[str]]) -> None:
    """A scale word after a placeholder must agree with the scale printed for that figure."""
    norm = normalise(text)
    for m in _PLACEHOLDER_RE.finditer(norm):
        after = _SCALE_AFTER_RE.match(norm, m.end())
        if after and figures.get(m.group(1)) and after.group(1).lower() not in figures[m.group(1)]:
            raise ClaimRejected("scale_mismatch")


def validate_claim(claim: DraftClaim, ticker: str, sources: Sources) -> Claim:
    ticker = ticker.upper()
    citations: list[Citation] = []
    labels: list[str] = []
    for c in claim.citations:
        info = sources.filing(c.accession)
        if info is None:
            raise ClaimRejected("unknown_page")
        if info.ticker != ticker:
            raise ClaimRejected("wrong_company")
        if len(normalise(c.excerpt)) < MIN_EXCERPT_CHARS:
            raise ClaimRejected("excerpt_too_short")
        page_text = sources.page_text(c.accession, c.page)
        if page_text is None:
            raise ClaimRejected("unknown_page")
        if not excerpt_on_page(c.excerpt, page_text):
            raise ClaimRejected("excerpt_not_on_page")
        citations.append(
            Citation(
                accession=c.accession,
                page=c.page,
                excerpt=c.excerpt,
                form=info.form,
                filing_date=info.filing_date.isoformat(),
            )
        )
        labels.append(period_label(info))
    excerpts = [c.excerpt for c in claim.citations]

    ids = [f.id for f in claim.figures]
    if len(ids) != len(set(ids)):
        raise ClaimRejected("bad_inputs")
    quoted: Quoted = {}
    scales: dict[str, set[str]] = {}
    rendered: dict[str, Figure] = {}
    for f in claim.figures:
        if isinstance(f, QuotedFigure):
            if f.citation >= len(claim.citations):
                raise ClaimRejected("bad_inputs")
            value = parse_value(f.value)
            if value is None:
                raise ClaimRejected("bad_value")
            excerpt = claim.citations[f.citation].excerpt
            if not figure_in_text(f.value, excerpt):
                raise ClaimRejected("figure_not_in_excerpt")
            known = {value.scale} - {""} or _scales_near(f.value, excerpt)
            known |= {w for w in _SCALES.split("|") if w in _unit_key(f.unit).split()}
            quoted[f.id] = (f, value, known)
            scales[f.id] = known if not value.scale else {"__none__"}  # value carries its own scale
            rendered[f.id] = Figure(value=f.value, unit=f.unit, period=labels[f.citation])
    for f in claim.figures:
        if isinstance(f, ComputedFigure):
            value_text, unit = _compute(f, quoted)
            periods = sorted({rendered[i].period or "" for i in f.inputs} - {""})
            rendered[f.id] = Figure(
                value=value_text,
                unit=unit,
                period="; ".join(periods) or None,
                computed=f"{f.op}({', '.join(f.inputs)})",
            )
            if f.op in ("percent_change", "ratio"):
                scales[f.id] = {"__none__"}
            else:
                scales[f.id] = set().union(*(scales[i] for i in f.inputs))

    if _GLUED_RE.search(claim.text):
        raise ClaimRejected("bad_placeholder")
    if any(name not in rendered for name in _PLACEHOLDER_RE.findall(claim.text)):
        raise ClaimRejected("unknown_placeholder")
    stripped = _PLACEHOLDER_RE.sub(" ", claim.text)
    if "{" in stripped or "}" in stripped:
        raise ClaimRejected("bad_placeholder")
    _check_placeholder_scales(claim.text, scales)
    if reason := _check_numbers(stripped, excerpts):
        raise ClaimRejected(reason)
    text = _PLACEHOLDER_RE.sub(lambda m: rendered[m.group(1)].value, claim.text)
    if contains_advice(text):
        raise ClaimRejected("advice")
    return Claim(text=text, citations=citations, figures=[rendered[i] for i in ids])


def validate(draft: Draft, ticker: str, sources: Sources) -> Note:
    dropped: Counter[str] = Counter()
    if draft.status == "not_found":
        return Note(status="not_found", disclaimer=DISCLAIMER)
    kept: list[Claim] = []
    for claim in draft.claims:
        try:
            kept.append(validate_claim(claim, ticker, sources))
        except ClaimRejected as exc:
            dropped[exc.reason] += 1

    summary = draft.summary
    if summary and (contains_advice(summary) or _check_numbers(summary, [c.text for c in kept])):
        summary = ""
        dropped["summary_dropped"] += 1
    if not kept:
        return Note(status="not_found", disclaimer=DISCLAIMER, dropped=dict(dropped))
    return Note(status="answered", summary=summary, claims=kept, disclaimer=DISCLAIMER, dropped=dict(dropped))


class FileSources:
    """Sources read from data/filings (offline use and tests)."""

    def __init__(self, filings: Iterable[Filing]) -> None:
        from app.ingest import filing_row

        self._pages: dict[tuple[str, int], str] = {}
        self._filings: dict[str, FilingInfo] = {}
        for f in filings:
            row = filing_row(f)
            self._filings[row.accession] = FilingInfo(
                accession=row.accession,
                ticker=row.ticker,
                form=row.form,
                filing_date=row.filing_date,
                report_date=row.report_date,
                amendment_no=row.amendment_no,
            )
            for p in f.extraction.pages:
                self._pages[(row.accession, p.index)] = p.text

    def page_text(self, accession: str, page: int) -> str | None:
        return self._pages.get((accession, page))

    def filing(self, accession: str) -> FilingInfo | None:
        return self._filings.get(accession)
