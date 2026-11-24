"""Offline tests for the draft validator: one case per rule (spec R6-R11)."""

from pathlib import Path
from typing import Any

import pytest

from app.draft import Draft, DraftClaim
from app.extract import extract_pages
from app.pages import Filing
from app.policy import DISCLAIMER
from app.validate import ClaimRejected, FileSources, Value, parse_value, validate, validate_claim

FIXTURE = Path(__file__).parent / "fixtures" / "mini_10k.html"
ORIGINAL = "0000002488-26-000018"
AMENDMENT = "0000002488-26-000021"
OTHER = "0001045810-26-000021"
AMENDMENT_HTML = (
    "<html><body><p>(Amendment No. 1)</p>"
    "<table><tr><td>Other income</td><td>0</td><td>12</td></tr></table>"
    "<p>Revenue was $130,497 million in 2025 and $60,922 million in 2024.</p></body></html>"
)
TABLE = "Revenue 1,234 1,000"  # page 5 of the fixture: "Revenue\t1,234\t1,000"


def filing(ticker: str, form: str, accession: str, html: str) -> Filing:
    meta: dict[str, Any] = {
        "ticker": ticker,
        "cik": 2488 if ticker == "AMD" else 1045810,
        "form": form,
        "filing_date": "2026-02-04",
        "report_date": "2025-12-27",
        "accession": accession,
        "primary_document": "doc.htm",
    }
    return Filing(meta=meta, extraction=extract_pages(html))


@pytest.fixture(scope="module")
def sources() -> FileSources:
    fixture = FIXTURE.read_text(encoding="utf-8")
    return FileSources(
        [
            filing("AMD", "10-K", ORIGINAL, fixture),
            filing("AMD", "10-K/A", AMENDMENT, AMENDMENT_HTML),
            filing("NVDA", "10-K", OTHER, fixture),
        ]
    )


def claim(text: str, figures: list[dict[str, Any]] | None = None, **cite: Any) -> DraftClaim:
    citation = {"accession": ORIGINAL, "page": 5, "excerpt": TABLE} | cite
    return DraftClaim.model_validate({"text": text, "citations": [citation], "figures": figures or []})


F1 = {"kind": "quoted", "id": "f1", "value": "1,000", "unit": "$ millions"}
F2 = {"kind": "quoted", "id": "f2", "value": "1,234", "unit": "$ millions"}


def computed(op: str, *inputs: str) -> dict[str, Any]:
    return {"kind": "computed", "id": "f3", "op": op, "inputs": list(inputs)}


def rejected(c: DraftClaim, sources: FileSources, ticker: str = "AMD") -> str:
    with pytest.raises(ClaimRejected) as exc:
        validate_claim(c, ticker, sources)
    return exc.value.reason


def test_valid_claim_is_rendered_with_computed_figure_and_labels(sources: FileSources) -> None:
    c = claim(
        "Revenue rose from {f1} million to {f2} million, up {f3}.",
        [F1, F2, computed("percent_change", "f1", "f2")],
    )
    out = validate_claim(c, "AMD", sources)
    assert out.text == "Revenue rose from 1,000 million to 1,234 million, up 23.4%."
    label = "source: 10-K for fiscal year ended 2025-12-27, filed 2026-02-04"
    assert [(f.value, f.period, f.computed) for f in out.figures] == [
        ("1,000", label, None),
        ("1,234", label, None),
        ("23.4%", label, "percent_change(f1, f2)"),
    ]
    assert (out.citations[0].form, out.citations[0].filing_date) == ("10-K", "2026-02-04")


@pytest.mark.parametrize(
    "op, inputs, expected",
    [
        ("difference", ("f1", "f2"), "234"),
        ("sum", ("f1", "f2"), "2,234"),
        ("ratio", ("f2", "f1"), "1.23x"),
        ("percent_change", ("f2", "f1"), "-19.0%"),
    ],
)
def test_operations(sources: FileSources, op: str, inputs: tuple[str, ...], expected: str) -> None:
    out = validate_claim(claim("Result {f3}.", [F1, F2, computed(op, *inputs)]), "AMD", sources)
    assert out.text == f"Result {expected}."


@pytest.mark.parametrize(
    "printed, value",
    [
        ("$1,234.5", Value(1234.5, 1, "", False)),
        ("(19,193)", Value(-19193.0, 0, "", False)),
        ("−3.05%", Value(-3.05, 2, "", True)),
        ("$130.5 billion", Value(130.5, 1, "billion", False)),
        ("$60,", None),  # truncated
        (",497", None),  # suffix
        ("fiscal 2025 and $60,922", None),  # more than one number
        ("Net income (in millions) 72,880", None),  # brackets that don't wrap the value
        ("(1,200", None),
    ],
)
def test_parse_value(printed: str, value: Value | None) -> None:
    assert parse_value(printed) == value


def test_units_differing_only_in_dollar_sign_are_the_same(sources: FileSources) -> None:
    f1 = F1 | {"unit": "millions"}
    out = validate_claim(claim("Up {f3}.", [f1, F2, computed("difference", "f1", "f2")]), "AMD", sources)
    assert out.text == "Up 234."


@pytest.mark.parametrize(
    "c, reason",
    [
        (claim("Revenue was {f2}.", [F2], accession=OTHER), "wrong_company"),
        (claim("Revenue was {f2}.", [F2], accession="0000000000-00-000000"), "unknown_page"),
        (claim("Revenue was {f2}.", [F2], page=99), "unknown_page"),
        (claim("Revenue was {f2}.", [F2], excerpt="Revenue 1,235 1,000"), "excerpt_not_on_page"),
        (claim("Revenue was {f2}.", [F2 | {"value": "1,235"}]), "figure_not_in_excerpt"),
        (claim("Revenue was {f2}.", [F2 | {"value": "1,23"}]), "bad_value"),
        (claim("Up {f3}.", [F1, F2 | {"unit": "%"}, computed("percent_change", "f1", "f2")]),
         "unit_mismatch"),
        (claim("Up {f3}.", [F1, computed("difference", "f1", "f9")]), "bad_inputs"),
        (claim("Up {f3}.", [F1, F2, computed("difference", "f1", "f2") | {"inputs": ["f1", "f2", "f1"]}]),
         "bad_inputs"),
        (claim("Revenue was {f7}.", [F2]), "unknown_placeholder"),
        (claim("Revenue was 2,500 million.", []), "unsupported_number"),
        (claim("Revenue was {f2} million; investors should buy.", [F2]), "advice"),
    ],
)  # fmt: skip
def test_rejections(sources: FileSources, c: DraftClaim, reason: str) -> None:
    assert rejected(c, sources) == reason


def test_division_by_zero(sources: FileSources) -> None:
    zero = {"kind": "quoted", "id": "f1", "value": "0", "unit": "$ millions"}
    twelve = {"kind": "quoted", "id": "f2", "value": "12", "unit": "$ millions"}
    c = claim("Up {f3}.", [zero, twelve, computed("percent_change", "f1", "f2")],
              accession=AMENDMENT, page=1, excerpt="Other income 0 12")  # fmt: skip
    assert rejected(c, sources) == "division_by_zero"


def test_years_and_quoted_numbers_in_text_are_allowed(sources: FileSources) -> None:
    out = validate_claim(
        claim("In fiscal 2025 revenue was 1,234 million, up from {f1}.", [F1]), "AMD", sources
    )
    assert out.text == "In fiscal 2025 revenue was 1,234 million, up from 1,000."


def test_amendment_label(sources: FileSources) -> None:
    twelve = {"kind": "quoted", "id": "f1", "value": "12", "unit": "$ millions"}
    c = claim("Other income was {f1}.", [twelve], accession=AMENDMENT, page=1, excerpt="Other income 0 12")
    label = validate_claim(c, "AMD", sources).figures[0].period
    assert label == "source: 10-K/A (Amendment No. 1) for fiscal year ended 2025-12-27, filed 2026-02-04"


def test_note_keeps_valid_claims_and_counts_dropped(sources: FileSources) -> None:
    draft = Draft(
        summary="Revenue reached 1,234 million.",
        claims=[claim("Revenue was {f2} million.", [F2]), claim("Revenue was 2,500 million.", []),
                claim("Revenue was {f2}.", [F2], page=99)],
    )  # fmt: skip
    note = validate(draft, "AMD", sources)
    assert note.status == "answered"
    assert [c.text for c in note.claims] == ["Revenue was 1,234 million."]
    assert note.summary == "Revenue reached 1,234 million."
    assert note.dropped == {"unsupported_number": 1, "unknown_page": 1}
    assert note.disclaimer == DISCLAIMER


def test_summary_with_unchecked_number_is_emptied(sources: FileSources) -> None:
    draft = Draft(
        summary="Revenue doubled to 2,468 million.", claims=[claim("Revenue was {f2} million.", [F2])]
    )
    note = validate(draft, "AMD", sources)
    assert note.summary == "" and note.dropped == {"summary_dropped": 1}


def test_advice_summary_is_emptied(sources: FileSources) -> None:
    draft = Draft(summary="AMD is a buy.", claims=[claim("Revenue was {f2} million.", [F2])])
    assert validate(draft, "AMD", sources).summary == ""


def test_all_claims_dropped_is_not_found(sources: FileSources) -> None:
    note = validate(Draft(claims=[claim("Revenue was 2,500 million.", [])]), "AMD", sources)
    assert (note.status, note.claims, note.dropped) == ("not_found", [], {"unsupported_number": 1})
    assert note.disclaimer == DISCLAIMER


def test_draft_not_found_passes_through(sources: FileSources) -> None:
    note = validate(Draft(status="not_found"), "AMD", sources)
    assert note.status == "not_found" and note.disclaimer == DISCLAIMER


# Bypasses found by the verifier (2026-11-24): each must be rejected.
REV = "Revenue was $130,497 million in 2025 and $60,922 million in 2024."
R1 = {"kind": "quoted", "id": "f1", "value": "$130,497", "unit": "$ millions"}
R2 = {"kind": "quoted", "id": "f2", "value": "$60,922", "unit": "$ millions"}


def rev(text: str, figures: list[dict[str, Any]] | None = None, excerpt: str = REV) -> DraftClaim:
    return claim(text, figures, accession=AMENDMENT, page=1, excerpt=excerpt)


@pytest.mark.parametrize(
    "c, reason",
    [
        (rev("Revenue was $2019 million."), "unsupported_number"),  # year-shaped amount
        (rev("Revenue was 2019%."), "unsupported_number"),
        (rev("Revenue was $130,497 billion."), "scale_mismatch"),
        (rev("Revenue was {f1} billion.", [R1]), "scale_mismatch"),
        (rev("Revenue was {f1}{f2}.", [R1, R2]), "bad_placeholder"),
        (rev("Revenue was {f1}2000 million.", [R1]), "bad_placeholder"),
        (rev("Revenue was 5{f1}.", [R1]), "bad_placeholder"),
        (rev("Net loss of -{f1}.", [R1]), "bad_placeholder"),
        (rev("Revenue was {{f1}}.", [R1]), "bad_placeholder"),
        (rev("Revenue doubled to five hundred billion dollars."), "unsupported_number"),
        (rev("Revenue grew 5 times.", excerpt="5"), "excerpt_too_short"),
        (rev("Total {f3}.", [R1 | {"value": "$130,497 million"}, R2, computed("sum", "f1", "f2")]),
         "unit_mismatch"),  # one value carries a scale word, the other does not
        (rev("Revenue was {f1}; investors\u00a0should\u00a0buy.", [R1]), "advice"),
        (rev("Revenue was {f1}; investors should b\u200buy.", [R1]), "advice"),
        (rev("Revenue was {f1}; we recommend that investors buy AMD.", [R1]), "advice"),
        (rev("Revenue was {f1}; shares look undervalued; accumulate.", [R1]), "advice"),
        (rev("Revenue was {f1}; investors should consider adding.", [R1]), "advice"),
        (rev("Revenue was {f1}; we rate the stock Outperform.", [R1]), "advice"),
    ],
)  # fmt: skip
def test_verifier_bypasses_are_rejected(sources: FileSources, c: DraftClaim, reason: str) -> None:
    assert rejected(c, sources) == reason


def test_matching_scale_word_and_lowercase_ticker_are_fine(sources: FileSources) -> None:
    c = rev("In 2025 revenue was {f1} million, up {f3} from {f2} million.",
            [R1, R2, computed("percent_change", "f2", "f1")])  # fmt: skip
    out = validate_claim(c, "amd", sources)
    assert out.text == "In 2025 revenue was $130,497 million, up 114.2% from $60,922 million."


def test_summary_with_year_shaped_amount_and_advice_is_emptied(sources: FileSources) -> None:
    draft = Draft(
        summary="Revenue jumped to $2050 million; we recommend that investors buy.",
        claims=[rev("Revenue was {f1} million.", [R1])],
    )
    note = validate(draft, "AMD", sources)
    assert note.summary == "" and note.dropped == {"summary_dropped": 1}


def test_negative_input_in_brackets_computes(sources: FileSources) -> None:
    loss = {"kind": "quoted", "id": "f1", "value": "(1,200)", "unit": "$ millions"}
    gain = {"kind": "quoted", "id": "f2", "value": "1,000", "unit": "$ millions"}
    c = claim("Change {f3}.", [loss, gain, computed("difference", "f1", "f2")],
              excerpt="Net loss (1,200) then 1,000")  # fmt: skip
    page = "Net loss (1,200) then 1,000 in the next year."
    one_page = FileSources([])
    one_page._filings = sources._filings  # reuse filing info; page text below
    one_page._pages = {(ORIGINAL, 5): page}
    assert validate_claim(c, "AMD", one_page).text == "Change 2,200."
