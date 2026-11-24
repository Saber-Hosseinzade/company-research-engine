# Plan: v0 M2, ingest filings and pages into Postgres

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-24. Committed as `intent/2026-11-24-v0-filings-agent/plan-m2.md` on branch
`feat/v0-m2-ingest`, one PR.

## Context

M0 fetches the 16 filings and splits them into pages, but the pages only exist in memory or as text
files. M2 stores filings and pages in the local Postgres (`compose.yaml`) with the metadata the spec
asks for (R3, R4), so M3 (citation validator), M4 (retrieval) and M5 (graph) read one store. Chunks and
embeddings stay in M4, where the two chunking strategies and the embedding model are chosen.
User decisions (2026-11-24): the database is the local Postgres in `compose.yaml`, not the dev
Supabase project. `DATABASE_URL` can point elsewhere later with no code change. Database tests run
with a separate `make test-db` against the local container; `make test` stays DB-free and CI is
unchanged.

## Files that change

- `pyproject.toml`, `uv.lock`: `psycopg[binary]` (psycopg 3, typed). `pgvector` waits for M4.
- `src/app/schema.sql` (new): idempotent DDL (`CREATE TABLE IF NOT EXISTS`), with no migration tool:
  - `filings`: accession (PK), ticker, cik, form, filing_date, report_date (fiscal period end),
    primary_document, amendment_no (null for 10-K), amends (accession of the 10-K, nullable FK),
    page_count, ingested_at.
  - `pages`: accession (FK), page_index, label, section, items_started (`text[]`), text,
    `tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED`, PK (accession,
    page_index), GIN index on tsv. Full-text search is ready for M4's hybrid search.
- `src/app/ingest.py` (new):
  - Pure part: `filing_row(filing)` and `page_rows(filing)` from `app.pages.Filing`. The amendment
    number is read from "Amendment No. N" on the first 3 pages of a 10-K/A. `link_amendments(rows)`
    sets `amends` to the 10-K with the same CIK and report date.
  - DB part: `apply_schema(conn)`, and `write_filing(conn, filing_row, page_rows)` in one transaction
    (delete the filing's pages, upsert the filing, insert its pages), so re-ingesting replaces rather
    than duplicates. `python -m app.ingest` (`make ingest`) loads `data/filings/` through
    `app.pages.load_filings` and logs counts and timings only (S9).
- `src/app/store.py` (new): `connect(settings)` and `db_page_store(conn)`, which returns the same
  `PageStore` mapping that `app.eval_v0` and `app.golden` use, read from the `pages` table.
- `Makefile`: `ingest`, `test-db`.
- `tests/test_ingest.py` (offline): row building, amendment number, amendment link.
- `tests_db/` (new, outside `testpaths`, so `make test` never collects it) with `conftest.py` and
  `test_store.py`. Run by `make test-db`. Each test session creates a throwaway schema
  (`test_<random>`), sets `search_path` to it, and drops it after. If the database is unreachable,
  it fails with "run `make db-up`" and does not skip. mypy covers `tests_db`.
- `intent/2026-11-24-v0-filings-agent/spec.md`: R3 notes that chunk storage moves to M4; testing
  section names `make test-db`.

## Order of work

1. `uv add 'psycopg[binary]'`.
2. `schema.sql` and `ingest.py` pure part, with `tests/test_ingest.py`. Inputs are the M0 fixture
   `tests/fixtures/mini_10k.html` and a small synthetic 10-K/A whose cover says "Amendment No. 1".
3. DB part and `store.py`, with `tests_db/test_store.py`.
4. `make db-up`, `make ingest` twice on the real 16 filings, and record the counts (step 6).
5. Spec note; plan departures if any; `PROJECT_CONTEXT.md`.

## Risks

- `make test-db` is not in CI, so DB code can break unnoticed. Mitigation: the PR states its result,
  and the verifier runs it. CI could add a Postgres service later if this bites.
- The generated `tsv` column uses the `english` configuration, which stems words. M4 may prefer
  `simple` for figures and names; that is a column change before chunks exist, so it's cheap.
- Linking amendments by CIK and report date assumes one original 10-K per fiscal period. That holds
  for the AMD 10-K/A; if it fails, `amends` stays null and the count shows it.
- Page text holds quotes from public filings in a local database only (S7, S8).

## Options not taken

- Alembic or other migrations: one idempotent schema file is enough for a local v0; revisit when
  deploying (roadmap step 2).
- An ORM such as SQLAlchemy: plain SQL with psycopg is smaller (S12) and easier to read.
- Chunks and embeddings now: they depend on M4's chunking and embedding choices.
- Postgres in CI, or pure tests only: the user chose a separate `make test-db`.

## Proof

- `tests/test_ingest.py`: filing row fields (form, filing_date, report_date, page_count); page rows
  match `extract_pages`; the amendment number is parsed ("Amendment No. 1" → 1, none for a 10-K);
  the amendment links to the 10-K with the same CIK and report date, and stays null when there is none.
- `tests_db/test_store.py` (`make test-db`): applying the schema twice works; writing a filing stores
  its pages; writing it again leaves the same row counts; `db_page_store` returns the stored text; a
  full-text query finds the page with "widgets" in the fixture; the amendment FK holds.
- Measured locally: `make ingest` twice gives 16 filings and 1,525 pages both times, with the AMD
  10-K/A having amendment_no 1 and amends = 0000002488-26-000018. `make golden-check` on the
  database page store also gives problems=0, which shows the store matches the files.
- `make check`, `make typecheck`, `make test` and `make test-db` pass; the `verifier` runs all four.

## Departures found while building (2026-11-24)

- `make golden-check` takes `--db` (`python -m app.golden --db`) to check against the database page
  store; the Proof's database check needs it.
- The throwaway schema is per test, not per session, so tests can't see each other's rows.
- `ingest()` writes 10-Ks before 10-K/As, so the `amends` foreign key always finds its row.
- A 10-K/A without a report date is not linked (verifier finding: two missing dates would match).
- Known limits: filings removed from `data/` stay in the database; `ingest` relies on each
  `conn.transaction()` committing, which holds when called as `make ingest` does.

## Measured (2026-11-24)

- `make ingest` twice: `filings=16 pages=1525` both times. The AMD 10-K/A: amendment_no 1, amends
  0000002488-26-000018. No filing lacks a report date. No `test_*` schemas left after `make test-db`.
- `python -m app.golden --db`: items=50 answerable=40 unanswerable=10 problems=0.
