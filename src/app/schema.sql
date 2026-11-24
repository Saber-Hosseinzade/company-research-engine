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
