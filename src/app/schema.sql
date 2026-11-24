-- Filings and pages (spec R3, R4). Idempotent: applied by `make ingest` and the DB tests.
-- Chunks and embeddings are added in M4.

CREATE TABLE IF NOT EXISTS filings (
    accession        text PRIMARY KEY,
    ticker           text NOT NULL,
    cik              bigint NOT NULL,
    form             text NOT NULL CHECK (form IN ('10-K', '10-K/A')),
    filing_date      date NOT NULL,
    report_date      date,              -- fiscal period end
    primary_document text NOT NULL,
    amendment_no     integer,           -- 10-K/A only
    amends           text REFERENCES filings (accession),
    page_count       integer NOT NULL,
    ingested_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pages (
    accession     text NOT NULL REFERENCES filings (accession) ON DELETE CASCADE,
    page_index    integer NOT NULL CHECK (page_index >= 1),
    label         text,
    section       text,
    items_started text[] NOT NULL DEFAULT '{}',
    text          text NOT NULL,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    PRIMARY KEY (accession, page_index)
);

CREATE INDEX IF NOT EXISTS pages_tsv_idx ON pages USING gin (tsv);

-- M4: chunks for retrieval, two strategies, each bounded by one page. The embedding column is added by
-- `make embed` once the model's dimension is known (app.chunks.ensure_embedding_column).
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;  -- never inside a test schema

CREATE TABLE IF NOT EXISTS chunks (
    id         bigserial PRIMARY KEY,
    strategy   text NOT NULL CHECK (strategy IN ('window', 'page')),
    accession  text NOT NULL,
    page_index integer NOT NULL,
    ordinal    integer NOT NULL,
    ticker     text NOT NULL,
    section    text,
    label      text,
    header     text NOT NULL,      -- company, form, fiscal year, section: prepended when embedding
    text       text NOT NULL,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding_model text,
    FOREIGN KEY (accession, page_index) REFERENCES pages (accession, page_index) ON DELETE CASCADE,
    UNIQUE (strategy, accession, page_index, ordinal)
);

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_scope_idx ON chunks (ticker, strategy);
