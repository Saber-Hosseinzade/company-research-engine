# Spec: v0 filings agent over 10-Ks

From: intent.md (Saber-Hosseinzade). Status: ready for review. Date: 2026-11-24.
Skills applied: security-policy, ai-design-standards.

## Requirements

Scope and ingestion

- R1. Only tickers NVDA, AMD, META, GOOG, AMZN are accepted; any other ticker is rejected with a 4xx
  error before any model call.
- R2. `make fetch` downloads, for each ticker, the primary document of every filing with form `10-K` or
  `10-K/A` and EDGAR filing date on or after 2024-01-01, into `data/filings/<cik>/<accession>/`. CIKs
  come from SEC's ticker file, not from code. Requests send `SEC_USER_AGENT` and stay at or below 10 per
  second. Re-running fetches only what is missing.
- R3. `make ingest` splits each filing into pages, stores each page's normalised text, and stores chunks
  with metadata: ticker, CIK, accession, form type, filing date, fiscal period end, page index, printed
  page label (when found), section (10-K Item, when found) (A9).
- R4. A `10-K/A` is stored as its own filing, with its form type and amendment number, and linked to the
  10-K it amends when EDGAR data allows.

Answering

- R5. `POST /research {ticker, question}` returns a note: a list of claims, each with one or more
  citations (accession, form type, filing date, page, excerpt), plus a short summary, a disclaimer and
  run metadata (token usage, timings).
- R6. Every citation's excerpt is found in the stored text of the cited page after normalisation (R7).
  A claim with any failing citation is dropped and counted in the run metadata; it never reaches the
  user (A7).
- R7. Normalisation for excerpt matching: Unicode NFKC, quotes and dashes folded, whitespace collapsed,
  case-insensitive, exact substring match, no fuzzy matching. Numbers must match digit for digit.
- R8. Every figure in a claim is either quoted (it appears in a cited excerpt) or computed. A computed
  figure is produced in code from quoted inputs with a fixed operation set (difference, percent change,
  ratio, sum); the model names the operation and the inputs, never the result.
- R9. Each figure is labelled in code with the period it covers, from the filing's fiscal period end
  (for example "fiscal year ended 2025-01-26"), not from the model.
- R10. When no claim survives validation, or retrieval finds no relevant chunk, the note says the filings
  in scope do not answer the question ("not found") (A8).
- R11. The note contains no buy, sell or hold, price target, rating or advice. Code rejects claims that
  match a deny-list of advice terms, and appends a fixed disclaimer that this is analysis, not advice,
  and that the model can be wrong (A12).
- R12. A question over 1,000 characters is rejected before any model call.

Interface

- R13. `GET /` serves a single page: company dropdown, question box, submit button, and the returned note
  with each citation linking to `GET /filings/<accession>/pages/<n>`, which shows the stored page text
  with the excerpt highlighted and a link to the original filing on EDGAR.
- R14. All model and filing text is rendered as escaped text, never as HTML. The page view shows the
  stored plain text, not the filing's original HTML (S10).
- R15. `docker compose up` starts the app and Postgres; the app binds to localhost only.

Operation

- R16. Every model and embedding call has a timeout, a retry limit and recorded token usage (A2).
- R17. Logs record IDs, counts, timings and token usage only: no question text, filing text or model
  output (S9).
- R18. Model output is parsed into Pydantic models; output that fails parsing is retried once, then the
  run returns an error (S11).
- R19. `make test` makes no network or model calls; model and embedding calls are faked (A10).
- R20. `make eval-v0` runs the golden set against the real model and reports the metrics in
  Evaluation; it is not part of `make test`.

## Design

**Flow.** `make fetch` (EDGAR → `data/`) → `make ingest` (pages, chunks, embeddings → Postgres) →
`POST /research` runs the LangGraph graph → note JSON → page renders it.

**Ingestion (code only, A4).**
- EDGAR: edgartools (user decision, M0) lists and downloads filings, handling the SEC identity, rate
  limit and paging; our code re-applies the scope rule (tickers, forms, filing date) before saving.
- Pages: HTML 10-Ks have no fixed pages. Rule (confirmed in M0, 2026-11-24): split at CSS page breaks
  (`page-break-before/after`, `break-before/after`), drop pages with no text, keep the sequential page
  index as the citation key, and record the printed page number as a display label when a footer
  number is found.
- Text: lxml (chosen in M0). Hidden iXBRL facts dropped; inline iXBRL tags unwrapped and their text kept;
  tables become one row per line, cells separated by tabs. Sections detected from "Item N." headings;
  contents rows ending in a page number, and pages with 10 or more headings, are not section starts.
- Chunking: two strategies, both bounded by page so every chunk maps to exactly one page:
  (a) fixed-size token windows with overlap; (b) paragraph groups within a detected section. M4
  measures both on retrieval recall (Evaluation) and keeps the better one.
- Storage (Postgres + pgvector): `filings`, `pages` (normalised text, page index, label),
  `chunks` (text, embedding, tsvector, metadata from R3). Embedding dimension comes from
  `EMBEDDING_DIM`; changing the embedding model means re-ingesting.

**Retrieval (code).** Hybrid search scoped to the request's ticker: pgvector cosine top-k and Postgres
full-text top-k, merged by reciprocal rank fusion. No reranker in v0.

**Model access.** OpenRouter's OpenAI-compatible API through the `openai` SDK, for chat (`MODEL_ID`) and
embeddings (`EMBEDDING_MODEL_ID`). One client module; nothing else knows the model (A1). Timeout 60 s,
2 retries (estimates, tuned in M5).

**Graph (LangGraph), one agent.**

| Node | Kind | Job |
| --- | --- | --- |
| scope | code | Check ticker and question (R1, R12); load the filings in scope. |
| plan_queries | model | Turn the question into 1–4 search queries. Judgement: questions use words the filings don't. |
| retrieve | code | Hybrid search per query; dedupe; top chunks with page text. |
| draft | model | Write claims as structured output: text, citations (chunk ID, excerpt), figures (quoted value or operation on figure IDs). Judgement: which evidence answers the question. |
| validate | code | R6–R9, R11: match excerpts, check quoted numbers, compute derived figures, label periods, drop failing claims. |
| finish | code | Not found (R10) or assemble the note, disclaimer, metadata. |

Failure path (A5): if validation drops more than half the claims, one more `draft` round with the
failures listed; then finish with what survives, or "not found". Tools are read-only (search over one
company's chunks); the model cannot choose a ticker or a command (S10). No side effects, so no approval
step (A6). No checkpointer or memory in v0.

**API and page.** FastAPI: `POST /research`, `GET /filings/<accession>/pages/<n>`, `GET /` (one static
HTML file with a small script, no framework). Compose adds an `app` service next to `db`.

**Settings (S2).** New: `SEC_USER_AGENT`, `EMBEDDING_MODEL_ID`, `EMBEDDING_DIM`. Added empty to
`.env.example` and typed in `config.py`. `MODEL_PROVIDER=openrouter` for v0.

**Dependencies (S12).** Estimated: `langgraph`, `fastapi`, `uvicorn`, `httpx`, `openai`, `psycopg`,
`pgvector`, one HTML parser. Each added in the milestone that needs it.

**AWS.** None in v0.

## How each constraint in the intent is met

| Constraint | Met by |
| --- | --- |
| Five companies, 10-K and 10-K/A filed from 2024-01-01 | R1, R2, R4 |
| Every claim cites filing and page; checked in code | R5, R6, R7 (A7) |
| Computed figures recalculated in code | R8 |
| No advice | R11 (A12) |
| OpenRouter for model and embeddings, as configuration | Model access, settings (A1, S2) |
| Public filings only, in `data/`, never committed | R2 (S7, S8) |
| No keys in repo | Settings in `.env.local` (S1) |
| Tests offline | R19 (A10) |
| EDGAR fair access | R2 |
| Simple question page | R13, R14 |
| Local only | R15, no AWS (S3–S6 not triggered) |

## Evaluation

- **Offline tests (M2–M6):** fetch and ingest on saved filing fixtures; excerpt matcher, arithmetic,
  period labels and advice filter on fixed cases; graph with a fake model, including a model that cites
  a wrong page (must be dropped) and one that states a computed number (must be replaced).
- **Golden set (M1, `golden/v0.jsonl`):** 40 answerable questions (8 per company: figure, change,
  narrative, two-filing, and the AMD 10-K/A) and 10 unanswerable ones (one out of scope and one advice
  request per company), each with the expected filing, page(s), a verbatim quote and key figures.
  `make golden-check` proves every quote and figure is on its page; a person reviews fairness.
- **Metrics (`make eval-v0`):**
  - citation validity of returned notes: target 100% (guaranteed by R6, checked anyway);
  - retrieval recall@10 of the expected page: used in M4 to choose the chunking strategy;
  - answer acceptance: expected figures present (or, for change questions, a value within 0.5% of the
    computed answer) and an expected page cited, where any page carrying the expected quote counts
    (code check, `app.eval_v0`); baseline in M5 (user decision 2026-11-24: M1 builds the harness only),
    target set then;
  - "not found" rate on unanswerable questions, with no advice wording: target set at the M5 baseline.
- **Models (user decision 2026-11-24):** baseline on Haiku 5.5 via OpenRouter, with one comparison run
  of the same golden set on Sonnet 5.5, both in M5.
- **Cost (A3, estimates, to be measured):** per question about 15k input and 2k output tokens; at
  $1 / $5 per million tokens that is about $0.03, so a 50-question eval run is about $1.50. Ingestion
  embeddings: about 17 filings × 150k tokens × 2 strategies ≈ 5M tokens, under $1 at typical embedding
  prices.

## Open questions from the intent

- OpenRouter chat model: Haiku 5.5 for the baseline, compared with Sonnet 5.5 in M5 (user decision
  2026-11-24). Embedding model: picked in M4; carried as concern 1.
- What a "page" is: answered in M0, rule above (concern 2).
- Excerpt normalisation: R7. Code/model split in the evidence check: code checks that the excerpt exists
  and the numbers match; whether the excerpt supports the claim is the model's judgement in v0
  (concern 3).
- 10-K/A citations: each citation names the document it came from and its form type and amendment
  number (R4, R5). No rule that an amendment replaces its original in v0 (concern 4).
- Fiscal period labels: R9.
- Answer acceptance target: set from the M5 baseline.

## Areas of concern

1. **Models unmeasured.** Chat model chosen (Haiku 5.5, Sonnet 5.5 compared) but unmeasured; no
   embedding model chosen; cost and quality above are estimates. Owner: user, at M4 and M5.
2. **Page rule: resolved in M0 (2026-11-24).** All 16 filings in scope have CSS page breaks. In the
   15 10-Ks, page count / last printed page number is 1.00–1.03 (the extra pages are cover and
   contents), and 98–99% of pages carry a printed label. 14 of 15 10-Ks find all 23 Items; META's
   2025 10-K misses Item 10. The AMD 10-K/A (21 pages, no printed numbers) is cited by page index only.
   Remaining risk: new filings with no page breaks; `make m0-report` lists them first. Owner: user.
3. **Excerpt check proves presence, not support.** A real excerpt can be cited for a claim it does not
   support. v0 relies on the model and the eval set for this. Owner: user; revisit after the M5 baseline.
4. **10-K/A overlap.** A note may cite both an original and its amendment for the same item without
   saying which is current. Owner: user; revisit if the golden set shows it.
5. **Bedrock path untested in v0.** `MODEL_PROVIDER=bedrock` stays in config but v0 builds and tests
   only OpenRouter, so A1 is met in structure but not exercised. Owner: user, at roadmap step 2.
6. **Data sent to OpenRouter.** Questions and public filing text go to OpenRouter and the model provider
   behind it. Public data only (S7), but OpenRouter's data retention depends on the chosen provider.
   Owner: user, when choosing the models.
7. **`.env.example` defaults.** S2 says empty values; the file already shows non-secret defaults. New
   settings follow S2; the existing ones stay as they are until that open item is decided. Owner: user.
8. **Unauthenticated local API.** S5 forbids unauthenticated endpoints; v0 binds to localhost only, which
   meets the intent's "local" but not S5 literally. Auth is due before any deployment. Owner: user.
