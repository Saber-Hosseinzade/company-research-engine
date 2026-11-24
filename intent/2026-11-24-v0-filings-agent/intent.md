# Intent: v0 filings agent over 10-Ks

Author: Saber-Hosseinzade. Status: draft. Date: 2026-11-24.

## Problem

Nothing is built yet. Today, answering a question about a listed company from its annual reports means
reading the 10-Ks by hand and copying figures and page references, and nothing checks that a quoted
figure or a computed change is correct. A model asked directly will answer, but its citations and
arithmetic can't be trusted.

## Proposed outcome

Given one of five companies (NVDA, AMD, META, GOOG, AMZN) and a question, a local service returns a
short research note built only from that company's 10-K filings on SEC EDGAR. Coverage is every 10-K
and amended 10-K/A filed on or after 2024-01-01, selected by EDGAR filing date (not fiscal year or
quarter):

- every claim and figure cites a filing and page, with the excerpt it relies on;
- code checks that each excerpt appears on the cited page; claims that fail are dropped or flagged;
- every computed figure (growth, difference, ratio) is recalculated in code from cited inputs;
- the note contains no buy/sell/hold, rating or advice.

Runs locally: one agent (LangGraph) behind FastAPI, with Postgres + pgvector, started with Docker Compose.
Model calls and embeddings go through OpenRouter (`MODEL_PROVIDER=openrouter`; embeddings via its
embeddings endpoint). A simple web page (M6) lets the user pick a company, type a question, and read the
note, with each citation linking to the cited filing page.

Success is measured on a golden set of questions over these five companies:

- 100% of citations in returned notes pass the code check (no unchecked citation reaches the user);
- answer acceptance rate: baseline measured at M1, target set then;
- all tests stay offline.

Built in PR-sized milestones, each with its own `plan.md`: M0 page/section extraction spike → M1 golden
set + eval harness → M2 ingestion → M3 citation validator → M4 embedding + hybrid retrieval (two chunking
strategies, measured) → M5 LangGraph graph → M6 FastAPI + Compose + simple question page.

## Affected users and systems

- The user, asking questions locally. Later the stock tool (roadmap step 3), not in v0.
- SEC EDGAR (public, rate-limited; needs a User-Agent with a contact email, kept in `.env.local`).
- OpenRouter: an API key in `.env.local`; prompts and filing text are sent to it.
- Local Postgres + pgvector (`compose.yaml`). No AWS resources.

## Constraints

- Public filings only; filings fetched by a script into `data/`, never committed.
- Model and embedding model are configuration, not code; no keys in the repo.
- Deterministic work (parsing, citation and arithmetic checks) in code, never the model.
- Tests offline: no EDGAR or OpenRouter calls in `make test`.
- Respect EDGAR's fair-access limits (max 10 requests/second).

## Out of scope

AgentCore or any cloud deployment; XBRL financials step; other filing types (10-Q, 8-K); developments
agent, supervisor, memory, approval; OCR; companies beyond the five; 10-Ks filed before 2024-01-01; any
UI beyond the simple question page (no login, history or styling work).

## Open questions

- Which OpenRouter chat and embedding models (the embedding model fixes the pgvector dimension).
- How a note cites a 10-K/A against the original 10-K it amends (an amendment may replace only some
  parts, such as Part III).
- What a "page" is in HTML 10-Ks (settled in M0).
- Excerpt-match normalisation (whitespace, Unicode, iXBRL tags) and the code/model split in the evidence
  check (settled in the spec).
- Fiscal years differ (NVDA ends late January): how a note labels the period a figure covers.
- Answer acceptance target, set from the M1 baseline.
