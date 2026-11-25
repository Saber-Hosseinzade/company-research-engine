# Plan: v0 M6, FastAPI, Compose and the question page

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-25. Committed as
`intent/2026-11-24-v0-filings-agent/plan-m6.md` on branch `feat/v0-m6-api`, one PR.

## Context

M5 built the agent (`app.agent.Agent`) and it is called only from `make eval-v0`. M6 puts it behind
an HTTP API with a single question page and runs it with `docker compose up`, finishing v0
(R5, R13–R15, R17). Two carry-overs: per-request usage must not mix concurrent requests (plan-m5
departures), and no user identifier goes to OpenRouter (concern 6).

## Design

**App (`src/app/api.py`, FastAPI):**
- `POST /research {ticker, question}` → `Note` JSON (R5). Each request builds its own `Agent` from a fresh
  `get_chat()`, `get_embedder()` and its own DB connection (`store.connect`), closed after the request.
  Each request then has its own usage counters, so concurrent requests can't mix tokens, with no lock.
  The handler is a plain `def`, so FastAPI runs it in its thread pool.
- Errors map to fixed messages, never model or filing text: `ScopeError` gives 422 with its message (R1,
  R12, before any model call). `ModelOutputError` gives 502. `ModelNotConfigured` and
  `DatabaseUnavailable` give 503. Anything else gives 500.
- `GET /filings/{accession}/pages/{n}?excerpt=…` returns server-rendered HTML: filing header (ticker, form,
  filing date, printed label), the stored page text in a `<pre>`, every piece escaped with
  `html.escape` (R14), and the excerpt wrapped in `<mark>`. The highlight is found by a whitespace- and
  case-tolerant regex on the raw text. If nothing matches, the page shows without a highlight. It also
  links to the original on EDGAR (`https://www.sec.gov/Archives/edgar/data/<cik>/<accession without
  dashes>/<primary_document>`, from the `filings` table). Unknown filing or page gives 404.
- `GET /` serves `src/app/static/index.html`: a company dropdown (the five tickers from `agent.py`'s
  allow-list), a question box with a 1,000-character limit and a submit button. A small inline script
  renders the note with `textContent` only, never `innerHTML` (R14). The note shows the summary, the
  claims with their figures and period labels, citation links to the page view, "not found", the
  disclaimer, and the cost and time.
- `GET /ping` → `{"status": "ok"}` (cheap health check; AgentCore needs it later).
- Logging (R17): uvicorn runs with `--no-access-log`, because the page URL carries excerpt text. The
  agent already logs ids, counts and timings only. The API adds one line per request: path kind,
  status and seconds.
- No user ID or `user` field is sent to OpenRouter (concern 6).

**Container:**
- `Dockerfile`: `python:3.12-slim` with `uv` copied from its official image, then `uv sync --frozen
  --no-dev`. It runs as a non-root user with `uvicorn app.api:app --host 0.0.0.0 --port 8000
  --no-access-log`. `.dockerignore` excludes `data/`, `.env*`, `.git` and caches.
- `compose.yaml`: an `app` service built from the Dockerfile, with `ports: ["127.0.0.1:8000:8000"]`
  (localhost only, R15, concern 8). It uses `env_file: .env.local` (optional, so the file never goes
  into the image), sets `DATABASE_URL=postgresql://app:app@db:5432/app` and `depends_on: db`
  (healthy). Ingest and embed still run from the dev container against the same `db`.
- `Makefile`: `serve` (uvicorn locally, reload) and `up` (`docker compose up -d --build`).

**Dependencies (ask first, S12):** `fastapi` and `uvicorn` (runtime). `httpx` is pulled in by `openai`
already and serves FastAPI's `TestClient`; it is added as a direct dev dependency only if needed.

## Files that change

- New: `src/app/api.py`, `src/app/static/index.html`, `Dockerfile`, `.dockerignore`,
  `tests/test_api.py`, `plan-m6.md`.
- Changed: `compose.yaml` (`app`), `Makefile` (`serve`, `up`), `pyproject.toml`/`uv.lock`, README (how to
  run), spec.md (as-built notes for R13–R15, concerns 6 and 8), `PROJECT_CONTEXT.md`.
- Reused: `agent.Agent`, `agent.check_scope`/`ScopeError`, `llm.get_chat`/`get_embedder`,
  `retrieve.DbRetriever`, `store.connect`/`DbSources`, `note.Note`.

## Proof

- `tests/test_api.py`, offline (A10). The agent factory and page lookup are injected through FastAPI
  dependencies and faked:
  - research returns the note JSON; an unknown ticker or a 1,001-character question gives 422 and
    the factory is never called;
  - `ModelOutputError` gives 502 and a missing model or database gives 503, with no model text in the
    body;
  - two requests get two separate agents (no shared usage);
  - the page view escapes `<script>` in page text, marks the excerpt, links to EDGAR, and returns 404
    for an unknown page;
  - `/` serves the page, and the page script contains no `innerHTML`.
- DB-backed page lookup: one test in `tests_db/` (`make test-db`).
- Manual, measured: `make up`, then `curl` `/ping` and one real question (about $0.002). Open `/` and a
  citation link in the browser. Record the time and cost in the PR and `PROJECT_CONTEXT.md` Results.
- `make check`, `make typecheck`, `make test` and `make test-db` pass, then the `verifier` runs.

## Options not taken

- Lock around one shared agent: simpler accounting but serialises users. A fresh agent per request
  costs one connection and two SDK clients, which is negligible locally.
- A front-end framework: one static file is what R13 asks for.
- Auth: deferred to deployment (concern 8); localhost bind only.

## Departures found while building (2026-11-25)

- `page_view` (the page lookup with its EDGAR link) lives in `app.store` next to `DbSources`, so
  `make test-db` covers it; `app.api` only renders.
- `app.api` calls `logging.basicConfig(INFO)`: uvicorn sets up only its own loggers, so the agent's and
  API's id/count/timing lines did not show without it.
- After the verifier: the page view also logs one line (path, status), and an unexpected error logs
  only its exception type, never its message or traceback, which could carry model or filing text
  (R17). Each has a test.
- `uvicorn` is installed without extras (no `uvloop`/`httptools`); enough for local v0.

## Measured (2026-11-25)

- `make up` builds and starts the app and db; `/ping` returns ok; an out-of-scope ticker returns 422 with
  no model call.
- One real question in the container (NVDA, change in Data Center revenue in fiscal 2026): answered,
  2 claims, 0 dropped, 9.2 s, 13,032 prompt and 1,085 completion tokens, $0.0369. The container took
  `MODEL_ID` from `.env.local`, which named Sonnet 5.5, not the Haiku 5.5 used for the baseline.
- The same question again after `MODEL_ID` in `.env.local` was set to Haiku 5.5: answered, 1 claim,
  0 dropped, 9.8 s, $0.0022.
- App logs carry ticker, status, counts, tokens and seconds only; no question or note text.
