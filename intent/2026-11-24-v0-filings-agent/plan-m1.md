# Plan: v0 M1, golden set and eval harness (no model calls)

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-24. Committed as `intent/2026-11-24-v0-filings-agent/plan-m1.md` on branch
`feat/v0-m1-golden-set`, one PR.

## Context

M1 gives v0 a fixed yardstick before anything answers questions: a golden set of questions over the 16
filings fetched in M0, and code that scores a research note against it. User decisions (2026-11-24):
**harness only, no model calls in M1**; the answer baseline moves to M5, measured on Haiku 5.5, with one
comparison run on Sonnet 5.5 (both via OpenRouter). No OpenRouter key is needed for M1.

## Files that change

- `src/app/pages.py` (new): load fetched filings and their pages from `data/filings/` (moves the loop
  out of `m0_report.py`, which then reuses it); `python -m app.pages` writes each page as a text file
  to `data/pages/<ticker>/<filing date>_<form>_<accession>/p<index>.txt` so pages can be read and
  searched while drafting questions.
- `src/app/note.py` (new): the research note schema (R5), Pydantic: status `answered` | `not_found`,
  summary, claims, each with citations (accession, page index, excerpt) and figures (value as printed,
  optional unit). M3 and M5 build on it.
- `src/app/citations.py` (new): R7 normalisation (NFKC, quotes and dashes folded, whitespace
  collapsed, case-insensitive) and `excerpt_on_page`. M3 builds the validator on it.
- `src/app/golden.py` (new): golden item schema and loader; `check_golden` verifies against the
  fetched pages that every expected quote and figure is on its expected page.
- `src/app/eval_v0.py` (new): `score(item, note, pages)` and `summarise(scores)`; an `Answerer` protocol
  that M5 implements. No CLI until M5.
- `golden/v0.jsonl` (new, committed): the golden set. Questions, accession, page index, figures as
  printed, and short quotes from public filings (S7); no full pages.
- `Makefile`: `pages`, `golden-check`.
- `tests/test_citations.py`, `tests/test_golden.py`, `tests/test_eval_v0.py`.
- `intent.md` and `spec.md`: the baseline and `make eval-v0` move from M1 to M5 (user decision);
  Sonnet 5.5 comparison in M5.

## Order of work

1. `pages.py`, refactor `m0_report.py` onto it, `make pages`. The M0 report output must not change.
2. `citations.py` with tests (R7): spaces, NBSP, curly quotes, en dashes, case; a number differing by
   one digit does not match.
3. `note.py` schema (R5, S11).
4. `golden.py` schema: `id`, `ticker`, `question`, `kind` (`answerable` | `unanswerable`), `category`
   (`figure` | `change` | `narrative` | `amendment` | `out_of_scope` | `advice`), `expected`: a list of
   refs `{accession, page, quote, figures}`, and for `change` questions `expected_value` with `unit`
   and `tolerance` (default 0.5%).
5. Draft the golden set: five subagents in parallel, one per company, each reading only its company's
   `data/pages/` files. Each writes 8 answerable items (3 `figure`, 2 `change` across two fiscal
   years, 2 `narrative`, 1 using two filings, or the 10-K/A for AMD) and 2 unanswerable items (data not
   in the filings in scope, such as next quarter's revenue or a fiscal year before the oldest filing,
   or a request for advice). That makes 50 items in total (40 answerable, 10 unanswerable). I merge
   them, run `make golden-check`, and fix or drop anything not on its page.
6. `eval_v0.py` scoring, per item:
   - answerable: `page_hit` (a citation on an expected accession and page), `figures_ok` (each expected
     figure appears in a claim or figure; for `change` items, a value within tolerance of
     `expected_value`), `accepted` = both;
   - unanswerable: `accepted` = status `not_found` and no advice terms;
   - every item: `citations_valid` = the share of excerpts found on their cited page (`excerpt_on_page`);
   - when the answerer returns the pages it retrieved: `recall_at_10` for the expected pages.
   `summarise` gives the acceptance rate (answerable), not-found rate (unanswerable), citation
   validity, recall@10 if present, and counts.
7. Update intent and spec; update `PROJECT_CONTEXT.md`.

## Risks

- Golden quality is the riskiest part: subagents can pick figures that are wrong or ambiguous.
  `golden-check` proves that each quote and figure is on its page, but not that the question is fair.
  **You review `golden/v0.jsonl` before merge** (owner: you).
- "Unanswerable" must really be unanswerable from the 16 filings. A 10-K shows 3 years of income
  statement and some tables show 5, so the subagents check those before writing an out-of-scope
  year.
- Figure matching by string is strict: "$60.9 billion" against "60,922" fails. Golden figures are
  written exactly as printed on the page, and scoring normalises only R7 differences. M5 shows
  whether this is too strict; loosening it is a scoring change, not a golden change.
- The note schema may change in M3/M5; the scoring code depends only on citations and figures.
- Subagent cost: 5 agents reading about 100 pages each (estimate). No model calls in the app itself.

## Options not taken

- Long-context baseline now (Haiku about $1.50 per run, Sonnet about $30): the user chose harness only.
- A model as judge of the answers: not needed while acceptance is figures plus pages, which code
  checks (A4).
- Golden set in `data/` (never committed): it must be versioned to compare runs; it holds only short
  quotes from public filings.
- YAML: needs a new dependency; JSONL with Pydantic is enough (S12).

## Proof

- `tests/test_citations.py`: the R7 cases above.
- `tests/test_golden.py` (offline): `golden/v0.jsonl` parses; ids are unique; 40 answerable (8 per
  company) and 10 unanswerable items; every `change` item has `expected_value`; accessions use the
  EDGAR format. Plus `check_golden` on a synthetic page store: passes and fails as expected.
- `tests/test_eval_v0.py` (fake notes, A10): a perfect note is accepted; a wrong page, a missing figure,
  a change value outside tolerance, and a made-up excerpt each fail their metric; `not_found` on an
  unanswerable item is accepted, an answer is not; an advice term fails; `summarise` rates on a small set.
- Local, measured: `make golden-check` passes on all 50 items against `data/` (run and recorded).
- `make check`, `make typecheck` and `make test` pass; the `verifier` subagent checks the diff against
  this plan.

## Departures found while building (2026-11-24)

- `page_hit` and `recall_at_10` also count any page that carries an expected quote, not only the golden
  page: the drafters found the same table row or sentence on several pages (10 of 40 answerable items
  have such extra pages). Accepted pages come from the quote, so a page with superseded text does not
  count.
- `citations_valid` is pooled: `ItemScore` keeps found and total excerpt counts, and the summary divides
  their sums.
- amd-08 (10-K/A): the ref to the original 10-K's superseded sentence was removed; amzn-02 asks only for
  the figure.
- `normalise` is cached (`lru_cache`), since page texts are normalised again for every item.
- An answerable item is accepted only with status `answered` and no advice wording, besides `page_hit`
  and `figures_ok`.
- After the verifier: change values are compared as magnitudes (the sign is often in words), a correctly
  rounded number counts ("3.1%" for 3.05), and "billion" converts for values in $ millions. The advice
  regex no longer flags "I recommend consulting an adviser" and now catches "is a buy", "would buy",
  "consider buying" and analyst ratings. goog-08 asks for the absolute change; nvda-06 figures carry `%`.

## Known limits (accepted for M1; revisit at the M5 baseline)

- Narrative items without figures are accepted on the cited page alone.
- For change items, any number in the note within tolerance counts, including an unrelated one.
- A figure printed in another form elsewhere (goog-03 "$91.4 billion" against 91,447; amd-01 16,635
  against "$16.6 billion") fails unless the note uses the golden form.
- `expected_pages` searches all pages, not only the item's company; no cross-company match today.

## Measured (2026-11-24)

- `make golden-check`: items=50 answerable=40 unanswerable=10 problems=0.
- A note citing exactly the golden pages and figures scores acceptance 1.0, not-found 1.0, citation
  validity 1.0 on all 50 items. 10 of 40 answerable items have extra valid pages carrying their quote.
