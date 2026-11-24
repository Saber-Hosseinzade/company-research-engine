# Plan: v0 M4, chunks, embeddings and hybrid retrieval

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-24. Committed as `intent/2026-11-24-v0-filings-agent/plan-m4.md` on branch
`feat/v0-m4-retrieval`, one PR.

## Context

M5's agent needs to find the right pages for a question. M4 builds that retrieval and measures it on
the golden set (recall@10 of the expected pages) before any chat model is involved. The spec asks for
two chunking strategies, measured, with the better one kept. User decision (2026-11-24): the
embedding model is `nvidia/nemotron-3.5-embed-1b:free` via OpenRouter. It is free, has a 32k context
and accepts no `dimensions` parameter. OpenRouter lists no data policy for this endpoint, and free
endpoints are rate-limited. It is a setting, so changing it later means only re-embedding.

## Design

**Chunks (both bounded by page, so every chunk maps to exactly one citable page, R3, A9):**
- `window`: line-aligned windows of about 1,500 characters with one line of overlap. Lines (and so
  table rows) are never split.
- `page`: the whole page as one chunk. Pages are at most about 7.4k characters (measured in M0),
  well within the 32k context.
- This replaces the spec's "paragraph groups within a section", which needs section detection inside
  a page that we don't have. Recorded in the spec. A whole-page chunk is the natural baseline because
  the page is the citation unit.
- The embedding input is the chunk text with a one-line header: company, form, fiscal year and
  section (for example "NVDA 10-K FY ended 2026-01-25, Item 7"). The stored `text` is the plain
  chunk text.

**Storage (`schema.sql` + `chunks`):** `CREATE EXTENSION IF NOT EXISTS vector`. The `chunks` table
holds: id, strategy, accession and page_index (FK to `pages`), ordinal, ticker, section, label,
text, a generated `tsv`, `embedding vector(N)` (null until embedded) and `embedding_model`.
- N is taken from the model's first response when the table is created. A different N or model later
  stops with a clear "re-create chunks" message.
- No ANN index. A few thousand chunks per company search exactly in milliseconds, and an exact search
  avoids HNSW's 2,000-dimension limit.

**Embedding client (`src/app/llm.py`):** the `openai` SDK pointed at OpenRouter
(`openrouter_base_url`, `OPENROUTER_API_KEY`), with a 60 s timeout and 5 retries using the SDK's
backoff, which also covers 429 (A2). It sends batches of 32 inputs and records the token usage
returned. `Embedder` is a protocol so tests use a fake (A10). It is the only module that knows the
model (A1).

**Commands:**
- `make chunk`: builds chunks for both strategies from `pages`. It is idempotent per filing and
  strategy.
- `make embed`: embeds chunks whose `embedding` is null. A rate limit or failure mid-run loses
  nothing; run it again to resume. It logs counts, tokens and timings only (S9).

**Retrieval (`src/app/retrieve.py`):** `search(conn, embedder, ticker, query, strategy, k=10)`.
- The vector search takes the top 30 chunks by cosine distance for the ticker. The full-text search
  takes the top 30 by `ts_rank` with `websearch_to_tsquery('english', …)`, for the ticker.
- The two lists are merged by reciprocal rank fusion (k = 60) in code, collapsed to pages (best chunk
  per page), and the top k pages are returned.
- The search is scoped to one company in SQL, so the model can never pull another company's page in
  (S10).

**Measurement (`src/app/eval_retrieval.py`, `make eval-retrieval`):** for each of the 40 answerable
golden questions, it measures recall@10 for each strategy (`window`, `page`) and each mode (vector,
full-text, hybrid). It reuses the M1 logic, where any page carrying the expected quote counts. A small
refactor moves recall@k out of `score()` into `app.eval_v0.recall_at_k`. Results print as a table and
are written to `data/evals/`. The strategy with the best hybrid recall@10 is kept, and the spec and
`PROJECT_CONTEXT.md` record the numbers.

**Settings (S2):** `EMBEDDING_MODEL_ID` goes in `.env.example` (empty) and `config.py`. You set it to
`nvidia/nemotron-3.5-embed-1b:free`, and set `OPENROUTER_API_KEY`, in `.env.local`.

## Files that change

- `pyproject.toml`, `uv.lock`: `openai` (OpenRouter's OpenAI-compatible API) and `pgvector` (psycopg
  adapter).
- `src/app/schema.sql`: the vector extension. The chunks DDL is applied by `app.chunks` once N is known.
- `src/app/chunks.py` (new): the chunkers (pure), chunk writing, and the `make chunk` and `make embed`
  entry points.
- `src/app/llm.py` (new), `src/app/retrieve.py` (new), `src/app/eval_retrieval.py` (new).
- `src/app/eval_v0.py`: `recall_at_k` extracted.
- `src/app/config.py`, `.env.example`, `Makefile` (`chunk`, `embed`, `eval-retrieval`).
- Offline tests: `tests/test_chunks.py`, `tests/test_retrieve.py` (fusion and page collapse, pure),
  `tests/test_llm.py` (batching and usage, with a fake transport).
- `tests_db/test_retrieval.py`: a 3-dimension chunks table with fake embeddings. Hybrid search ranks
  the expected page first and never returns another ticker's page.
- `spec.md`: the chunking change, the embedding model, and the measured result.

## Order of work

1. Settings, dependencies and `llm.py`, with tests.
2. Chunkers and `make chunk`, with tests.
3. Schema, `make embed` (resumable), and `tests_db`.
4. `retrieve.py`, with tests.
5. `eval_retrieval.py`. Run `make chunk`, `make embed` and `make eval-retrieval` on the real data, and
   record the results.
6. Spec, plan departures, `PROJECT_CONTEXT.md`.

## Risks

- **Free endpoint rate limits** may slow `make embed` or stop it partway. Resume handles a stop.
  If the daily limit can't cover about 3M tokens (estimate), I'll stop and tell you rather than
  switch model.
- **Unknown data retention.** Only public filing text and golden questions are sent in M4. User
  questions start in M6; spec concern 6 already names this, and I'll note the model there.
- **Dimensions over 2,000** don't matter here, because there's no ANN index.
- **The model may disappear** (a dated free endpoint). Because it is configuration, changing it means
  `make embed` again.
- **Recall may be low for table-number questions.** Full-text search on figures helps; the
  measurement shows it per mode.

## Options not taken

- An HNSW or IVFFlat index: not needed at this size, and it limits the dimension.
- A reranker: not in v0 (spec), and availability is an open item.
- LangChain retrievers: plain SQL and a small fusion function are clearer (S12).
- Paragraph-within-section chunks: we have no in-page section structure. Whole page is the honest
  second strategy.

## Proof

- Offline:
  - window chunks never cross a page, never split a line, overlap by one line, and cover every line;
  - page chunks equal the page;
  - RRF fusion orders and collapses to pages correctly;
  - `recall_at_k` matches M1's results;
  - the embedding client batches 70 inputs as 32 + 32 + 6, returns vectors in order, and sums usage.
- `make test-db`:
  - the chunks table takes the right dimension, and a mismatch stops with a message;
  - `embed` fills only rows that are still null;
  - hybrid search returns the expected page first and is scoped to the ticker.
- Measured locally:
  - chunk counts per strategy, embedded tokens, wall time and cost (0 for a free model);
  - recall@10 per strategy and mode on 40 questions.
- `make check`, `make typecheck`, `make test` and `make test-db` pass; the `verifier` checks the diff
  against this plan.

## Departures found while building (2026-11-24)

- No `pgvector` Python package: vectors go to Postgres as text with `::vector`, so the adapter isn't
  needed (S12).
- The embedding column is added by `make embed` once the model's dimension is known; `make chunk`
  creates the table without it.
- `make chunk` skips a filing and strategy that already have chunks (keeping their embeddings);
  `python -m app.chunks chunk --rebuild` rebuilds.
- Full-text search ORs the query terms (`plainto_tsquery` with `&` replaced by `|`); with AND semantics
  a full question rarely matches anything.
- Missing settings print a one-line message instead of a traceback.
- `CREATE EXTENSION vector WITH SCHEMA public`, and the DB tests put `public` on their search path:
  the first test run had created the extension inside a throwaway test schema, and dropping that
  schema removed it (found by `make test-db` failing after the real `make chunk`).
- `search(conn, ticker, strategy, query, query_vector, model, mode, k)`: it takes a precomputed query
  vector and its model (so a run embeds all questions once) and a `mode` (vector, fts, hybrid) for
  the measurement. It uses only chunks with `embedding_model` equal to `model`.
- After the verifier: `make embed` stops before any API call when stored chunks come from another
  model, and prints the reset command instead of a traceback; it applies the schema first, so it works
  before `make chunk`. The window chunker skips the overlap when the overlap line and the next line
  don't fit together. The chunks DDL lives in `schema.sql`; only the embedding column is added later.
  The eval JSON records query tokens.

## Measured (2026-11-24)

- `make chunk`: window=5,940, page=1,525 chunks, 1.2 s.
- `make embed`: 7,465 chunks, 234 requests, 2,922,255 tokens, 500 s, no rate-limit stop; the
  model returns 2,048 dimensions; cost $0 (free endpoint).
- `make eval-retrieval`, recall@10 over 40 answerable golden questions (re-run after the model filter,
  same numbers):
  window 0.825 vector / 0.650 full-text / 0.725 hybrid; page 0.838 / 0.713 / 0.838. **Kept: page
  strategy, hybrid search.** The misses: nvda-05, nvda-08, amd-04, amd-05, meta-02, meta-04, meta-08
  (5 change questions, 2 headcount).
