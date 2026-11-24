# Plan: v0 M0, fetch 10-Ks and test the page and section rule

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-24. Committed as `intent/2026-11-24-v0-filings-agent/plan-m0.md` on `intent/v0-filings-agent`.

## Context

The spec proposes citing 10-K pages by splitting HTML at CSS page breaks (concern 2), and leaves the HTML
parser open. M0 answers both on the real filings before ingestion (M2) builds on them. Fetching is needed
for that, so `make fetch` (R2) is built here in full; extraction is built as reusable code, not a
throwaway script, and M2 stores its output.

## Files that change

- `src/app/config.py`, `.env.example`: `SEC_USER_AGENT` (empty in `.env.example`, S2).
- `src/app/edgar.py` (new): EDGAR client and `python -m app.edgar` entry point.
- `src/app/extract.py` (new): HTML filing → pages (index, label, section, text).
- `src/app/m0_report.py` (new): runs extraction over `data/filings/` and prints the measurements.
- `Makefile`: `fetch`, `m0-report` targets.
- `pyproject.toml`, `uv.lock`: `edgartools`, `lxml`; dev `lxml-stubs` (mypy strict). If edgartools
  ships no type hints, a narrow mypy override for it only.
- `tests/test_edgar.py`, `tests/test_extract.py`, `tests/fixtures/*.html` (small synthetic filings).
- `intent/2026-11-24-v0-filings-agent/spec.md`: concern 2 and parser choice updated with the result.

## Order of work

1. Settings: add `sec_user_agent: str | None`; fetch fails with a clear message when it is unset (S1, S2).
2. `edgar.py` (R2), a thin layer over **edgartools** (user decision 2026-11-24: fetch only):
   - `set_identity(SEC_USER_AGENT)`; edgartools handles the 10 requests/second limit and EDGAR paging.
   - Point edgartools' local cache into `data/` (its local-data setting; exact name confirmed when
     implementing) so nothing is written outside `data/` (S8).
   - For the five tickers only (R1): `Company(ticker)` filings with form `10-K` or `10-K/A` and
     filing date ≥ 2024-01-01; our own code re-checks form and date before saving, so the rule is
     ours and tested, not only the library's filter.
   - Save the primary document HTML to `data/filings/<cik>/<accession>/<primaryDocument>` and
     `meta.json` beside it (ticker, CIK, form, filing date, period of report, accession, primary
     document; R4 needs the form and report date). Skip filings already on disk.
   - The edgartools calls sit behind one small function, so tests feed fake filings (A10).
   - Logs: ticker, accession, counts, timings only (S9).
3. `extract.py` (R3, page part), with lxml:
   - Drop `ix:header` (hidden iXBRL facts) and other `display:none` blocks; unwrap remaining `ix:*` tags,
     keeping their text.
   - Page split: start a new page at any element whose style has `page-break-before/after` or
     `break-before/after` with `always` or `page`; page index counts from 1.
   - Text: block elements end a line; table rows one per line, cells separated by tabs; whitespace
     collapsed within lines.
   - Label: the printed page number from the last short line of a page (`12`, `F-7`, `ii`), else none.
   - Section: "Item N[A]." headings (Part I–IV); a page carries the section open at its start and
     lists any that begin on it.
4. `m0_report.py` and `make m0-report`: per filing, the number of pages, pages with a label, empty pages,
   median and max page length, Items found and missing (of 1, 1A, 7, 7A, 8 for 10-K); filings with no
   page breaks are listed first. For 10-Ks it also lists edgartools' Items next to ours, so we see
   where our heading detection misses. Output is printed, not committed (S8).
5. Run `make fetch` and `make m0-report` on the real filings; write the result into spec.md (concern
   2, parser) and dated into Results in `PROJECT_CONTEXT.md`.

## Decision rule for the spike

Keep the CSS page-break rule if every filing has page breaks, and the page count is within about 5% of the
last printed page number for 10-Ks that print numbers. Otherwise propose a fallback in spec.md and ask
you before M2. 10-K/As (often Part III only) are reported but not held to the Items check.

## Risks

- Riskiest: filings whose page breaks are not in inline styles (for example CSS classes), giving one huge
  page. The report lists them first; handling class-based breaks is a small extension if needed.
- EDGAR blocks requests without a valid User-Agent; you set `SEC_USER_AGENT` in `.env.local`.
- edgartools' dependency footprint is unmeasured (likely pandas and more); I report what `uv add`
  pulls in. It matters for the AgentCore image later, not for M0.
- edgartools' API changes often (active project); `uv.lock` pins the version.
- Hidden iXBRL text leaking into pages would break excerpt matching later (M3); tested with a fixture.

## Options not taken

- selectolax: faster, but lxml is mature, typed through `lxml-stubs`, and speed doesn't matter for about
  17 filings. I'd try selectolax only if lxml fails on a filing.
- Docling: heavy image, ARM64 unknown (open item); not needed for HTML.
- Own EDGAR client over the submissions JSON: dropped for edgartools (user decision), which already
  handles identity, rate limits and paging, and is a candidate for XBRL in roadmap step 4.
- edgartools' Item sections as our sections: would need joining its text to our pages; compared in
  the report instead.

## Proof

- `tests/test_edgar.py` (fake filings source, no network, A10): only the five tickers; form and date
  filter, including a 10-K/A kept and a 2023 10-K and a 10-Q left out; files and `meta.json` written
  under `data/`-style tmp dir; skips filings on disk; stops with a message when `SEC_USER_AGENT` is
  unset.
- `tests/test_extract.py` (synthetic HTML): page split on both break styles; `ix:header` text absent;
  `ix:nonFraction` value present; table rows tab-separated; labels `12`, `F-7` and none; Item headings
  assigned to pages.
- `make check`, `make typecheck`, `make test` pass; the `verifier` subagent checks the diff against this plan.
- Measured: the `make m0-report` table on the real filings, recorded with its date.
