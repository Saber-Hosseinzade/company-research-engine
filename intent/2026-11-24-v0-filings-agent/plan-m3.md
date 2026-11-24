# Plan: v0 M3, citation and figure validator (code only)

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-24. Committed as `intent/2026-11-24-v0-filings-agent/plan-m3.md` on branch
`feat/v0-m3-validator`, one PR.

## Context

The intent's core promise is that citations and arithmetic are checked in code, never by the model. M3
builds that check. It takes the model's draft (the structured output M5's `draft` node will produce)
and turns it into the final `Note`. Claims whose citations or numbers don't check out are dropped,
computed figures are calculated in code, each figure is labelled with its source filing's period, and
advice is removed. There are no model calls; this is all plain code (A4), tested offline.

## Design

**Draft (what the model emits, `src/app/draft.py`, Pydantic, S11):**
- `DraftClaim`: `text` with figure placeholders such as `"Revenue grew {f3} to {f2}."`, `citations`
  (accession, page, excerpt), `figures`.
- `DraftFigure`: `id` (`f1`…), and either `quoted` (`value` as printed, `unit`, the index of the citation
  it comes from) or `computed` (`op` ∈ difference | percent_change | ratio | sum, `inputs`: ids of quoted
  figures). The model never writes a computed number (R8).
- `Draft`: `summary`, `claims`, or `status: "not_found"`.

**Validator (`src/app/validate.py`, `validate(draft, ticker, sources) -> Note`).** Each claim either
survives whole or is dropped with a reason:
1. *Citations (R6, R7):* every citation must be on a filing of the request's ticker (S10) and its
   excerpt must be on the cited page (`app.citations.excerpt_on_page`). Reasons: `wrong_company`,
   `unknown_page`, `excerpt_not_on_page`.
2. *Quoted figures (R8):* the value must appear in its citation's excerpt (`figure_in_text`). Reason:
   `figure_not_in_excerpt`.
3. *Computed figures (R8):* inputs must be quoted figures in the same claim with the same unit; values
   are parsed from the printed form (`$`, commas, `%`, and `(1,234)` → −1234). Code computes and
   formats the result: difference and sum in the input unit, percent change to one decimal in `%`,
   ratio to two decimals as `x`. Reasons: `bad_inputs`, `unit_mismatch`, `division_by_zero`.
4. *Placeholders:* every `{fN}` in the text must name a figure. Reason: `unknown_placeholder`. Code
   renders the text with the validated values.
5. *Stray numbers:* any other number in the claim text (outside placeholders) must appear in one of
   the claim's excerpts. Years (1990–2099) are exempt. Reason: `unsupported_number`. This stops the
   model from writing a computed number as plain text.
6. *Advice (R11):* a claim matching the advice deny-list is dropped (`advice`). The list moves from
   `app.eval_v0` to `src/app/policy.py`, together with the fixed disclaimer, so both use the same list.
7. *Period label (R9):* each figure gets a label from its citation's filing, not from the model:
   "10-K for fiscal year ended 2025-01-26, filed 2025-02-26", or for an amendment
   "10-K/A (Amendment No. 1) for …". This names the source filing's period. The column year in a
   multi-year table stays in the claim text, checked only as text. (Recorded as a spec note.)
- *Summary:* kept only when it has no advice and every number in it appears in a surviving claim;
  otherwise it is replaced by an empty summary, counted as `summary_dropped`.
- *Result (R6, R10, R11):* `Note` with the surviving claims, disclaimer and `dropped` counts by
  reason. If no claim survives, or the draft says `not_found`, status is `not_found` (A8).

**Sources (`Sources` protocol in `validate.py`):** `page_text(accession, page)` and
`filing(accession)` → ticker, form, filing date, report date and amendment number. Two
implementations: `FileSources` (from `app.pages.load_filings` plus `app.ingest.filing_row`) for tests
and offline use, and `DbSources` (from the `filings` and `pages` tables) in `app.store`, tested by
`make test-db`.

## Files that change

- `src/app/draft.py`, `src/app/validate.py`, `src/app/policy.py` (new).
- `src/app/note.py`: `Citation` gains `form` and `filing_date` (R5). `Figure` gains `period` and
  `computed` (for example `"percent_change(f1, f2)"`). `Note` gains `disclaimer` and `dropped`
  (reason → count). New fields get defaults, so `app.eval_v0` keeps working.
- `src/app/eval_v0.py`: imports the advice list from `app.policy`.
- `src/app/store.py`: `DbSources`.
- `tests/test_validate.py` (offline, `FileSources` over the M0 fixture and a synthetic second filing),
  `tests_db/test_store.py` (`DbSources`).
- `spec.md`: R9 note (the label names the source filing's period).

## Order of work

1. `policy.py` (advice list moved) and the `note.py` fields; existing tests stay green.
2. `draft.py` schema.
3. `validate.py` steps 1–7, test by test.
4. `FileSources` and `DbSources`.
5. Spec note, plan departures, `PROJECT_CONTEXT.md`.

## Risks

- The stray-number rule may drop good claims: for example "Item 7", "Q4", or "3 years" that aren't in
  the excerpt. Years are exempt; M5's baseline will show the drop rate by reason, and the rule can
  loosen then.
- Units are strings from the model; "$ millions" vs "millions" would block a valid computation. I
  normalise case and whitespace and strip a leading `$`. Mismatches are counted, so they show up in M5.
- A quoted value printed as "$60.9 billion" and a table value "60,922" can't be combined (different
  units). That's correct behaviour, but it limits some answers.
- The period label describes the source filing, not the figure's column. The spec note makes that
  explicit; the label wording says "source:" to avoid overclaiming.

## Options not taken

- Free-text claims with numbers matched afterwards: can't tell a computed number from a made-up one;
  placeholders make R8 enforceable.
- Letting the model state the period of each figure: R9 says code, not model.
- A tolerance for computed results stated by the model: the model never states them.

## Proof

- `tests/test_validate.py`, one test per rule. Each case below must hold:
  - a valid claim survives and its text is rendered;
  - a citation to another company's filing is dropped (`wrong_company`);
  - a missing page is dropped (`unknown_page`);
  - an excerpt not on its page is dropped (`excerpt_not_on_page`);
  - a quoted figure missing from its excerpt is dropped;
  - percent change, difference, ratio and sum are computed and formatted, and a negative input in
    parentheses parses;
  - a unit mismatch, a missing input and a division by zero are each dropped with their reason;
  - an unknown placeholder is dropped;
  - a stray number is dropped, while a year is allowed;
  - advice is dropped and the disclaimer is always present;
  - period labels are correct for a 10-K and for a 10-K/A;
  - a summary with an unsupported number is emptied;
  - all claims dropped gives `not_found`;
  - the `dropped` counts are right.
- `tests_db/test_store.py`: `DbSources` returns the same filing info and page text as `FileSources`.
- `make check`, `make typecheck`, `make test` and `make test-db` pass; the `verifier` checks the diff
  against this plan.

## Departures found while building (2026-11-24)

- Duplicate figure ids in a claim are rejected as `bad_inputs`.
- A citation to an accession that isn't stored is `unknown_page`, the same as a missing page.
- Operations are defined in `draft.py`: difference(a, b) = b − a; percent_change = (b − a) / |a| × 100;
  ratio = a / b; sum. A quoted citation index out of range is `bad_inputs`.
- Hardening after the verifier showed bypasses (each now has a regression test):
  - a quoted value must be one printed number (`$60,922`, `(1,200)`, `10.5%`, `$130.5 billion`);
    otherwise `bad_value`. Brackets make it negative only when they wrap it;
  - the scale word is part of a value; a scale word in the text that contradicts the excerpt or the
    unit is `scale_mismatch`;
  - a placeholder touching a digit, a sign or another placeholder, or a stray brace, is `bad_placeholder`;
  - only a bare year is exempt from the number check (not `$2019 million` or `2019%`); spelled-out
    amounts with a scale word are `unsupported_number`;
  - excerpts shorter than 15 characters are `excerpt_too_short`;
  - computed inputs must share unit, scale and percent for every operation, ratio included;
  - the advice check runs on normalised text without invisible characters (`policy.contains_advice`)
    and covers the paraphrases found; the eval uses the same function;
  - the ticker is compared upper-case.

## Known limits (accepted; revisit with the M5 baseline)

- A number that appears anywhere in a cited excerpt is accepted in the text, even if the claim
  attaches it to the wrong line item.
- The unit of a table value is the model's word; code checks consistency, not truth.
- percent_change from a negative base follows the |a| rule and can read oddly.
- Look-alike letters from other scripts can still evade the advice phrase list.

## Measured (2026-11-24)

- A hand-written draft for golden item nvda-04, validated against the database (`DbSources`): the
  percent change is computed in code as 105.5% and labelled "source: 10-K for fiscal year ended
  2026-01-25, filed 2026-02-25". A second claim with a made-up number is dropped (`unsupported_number`).
  The eval scores the note as accepted.
