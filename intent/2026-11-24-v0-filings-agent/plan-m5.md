# Plan: v0 M5, the filings agent (LangGraph) and the first answer baseline

From: intent.md and spec.md (intent/2026-11-24-v0-filings-agent/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-24. Committed as `intent/2026-11-24-v0-filings-agent/plan-m5.md` on branch
`feat/v0-m5-agent`, one PR.

## Context

M0 to M4 built every part except the one that answers: filings and pages, the golden set, the
validator and retrieval (window chunks + hybrid, recall@10 0.863). M5 joins them into the agent the
spec describes. It also runs the first end-to-end answer baseline on the golden set, on Haiku 5.5
with one comparison run on Sonnet 5.5 (user decision), and sets the answer targets from it. The API
and page come in M6; M5 is called from Python and `make eval-v0`.

## Design

**Graph (`src/app/agent.py`, LangGraph `StateGraph`), as in the spec:**

| Node | Kind | Job |
| --- | --- | --- |
| scope | code | The ticker must be one of the five and the question at most 1,000 characters (R1, R12); otherwise an error before any model call. |
| plan_queries | model | 1 to 4 search queries for the question (structured output). |
| retrieve | code | Embeds the queries and the question in one call, then runs hybrid search (window, k=10) for each. It merges pages by best fused rank and keeps the top 8 with their full text, form, filing date and fiscal year. |
| draft | model | Writes a `Draft` (M3 schema) from those 8 pages only (structured output). |
| validate | code | `app.validate.validate` with `DbSources` (R6–R11). |
| finish | code | Returns the note with run metadata: tokens, cost, timings, retrieved pages and dropped counts (R5, R16). |

- **Failure path (A5):** if validation drops more than half the claims, `draft` runs once more with the
  drop reasons listed, then the graph finishes with whatever survives, or "not found" (R10).
- **Bad model output (R18):** output that fails Pydantic parsing is retried once with the error, then
  the run returns an error (S11).
- **Untrusted input (S10):** the model never picks the company. Filing text is treated as data, never
  as instructions; the draft prompt says so and the validator enforces it.
- **No memory:** no checkpointer in v0.
- **Testable:** the graph is built with its dependencies injected (`ChatModel`, `Retriever`,
  `Sources`), so tests run it offline with fakes (A10).

**Chat client (`src/app/llm.py`, the only module that knows models, A1):**
- It uses the `openai` SDK on OpenRouter with `response_format` set to the JSON schema of the
  Pydantic model, then validates the reply with that model.
- The OpenRouter provider routing is enforced in code: `data_collection: "deny"` (no provider that
  trains on data), `zdr: true` (zero-retention providers only) and `require_parameters: true` (the
  provider must support structured output). Option names are confirmed against OpenRouter's docs
  first.
- Usage accounting is on, so each call records prompt and completion tokens and OpenRouter's cost
  (A2, A3).
- Timeout 120 s, 5 SDK retries and the rate-limit waits from M4 (A2).
- `MODEL_PROVIDER=bedrock` raises a clear "not built in v0" error (spec concern 5).

**Prompts (`src/app/prompts.py`):** fixed text, versioned in Git. The draft prompt explains the
placeholder and figure rules, says to quote excerpts verbatim from the given pages, to answer
`not_found` when the pages don't answer, and that there is no advice. Pages are passed as delimited
data with their accession and page index.

**Eval (`make eval-v0 [MODEL_ID=…]`, `src/app/eval_v0.py` CLI):**
- It runs the agent on all 50 golden items, one at a time, with `app.eval_v0.score`.
- It prints acceptance (answerable), not-found (unanswerable), citation validity, recall@10 of the
  retrieved pages, drop reasons, tokens, cost and time.
- Per-item results go to `data/evals/answers-<model>-<time>.json`: ids, scores, drop counts, no text
  in logs (S9). The notes are saved there for review.

## Files that change

- `pyproject.toml`, `uv.lock`: `langgraph`.
- `src/app/agent.py`, `src/app/prompts.py` (new). `src/app/llm.py` (chat). `src/app/retrieve.py`
  (`DbRetriever`: page texts for the top pages). `src/app/eval_v0.py` (CLI).
- `Makefile`: `eval-v0`.
- `tests/test_agent.py` (offline, fake chat and retriever, `FileSources`), `tests/test_llm.py`
  (chat: schema, routing options, usage, one parse retry).
- spec.md: measured baseline, targets, cost per run (A3), concern 1 and 6 updates.

## Order of work

1. Check OpenRouter's provider-routing and usage options in its docs, then build the chat client
   with tests.
2. Prompts, then the graph with fakes and its tests.
3. `DbRetriever` and the `make eval-v0` CLI.
4. A smoke run on 3 golden items with Haiku. Read the notes before spending on the full set.
5. Full baseline: Haiku on 50 items, then Sonnet on 50 items. Record both.
6. Propose targets from the baseline (you decide), then update the spec, plan departures and
   `PROJECT_CONTEXT.md`.

## Cost (A3, estimates until measured)

- Per question: about 1k input and 150 output tokens to plan queries, plus about 12k input and 1.5k
  output tokens to draft, with a retry on some questions.
- Haiku 5.5 ($0.10 / $0.50 per M): about $0.002 per question, about $0.15 per 50-item run.
- Sonnet 5.5 ($2 / $10 per M): about $0.04 per question, about $2.50 per run.
- Query embeddings: under a cent.
- Total for the smoke run and both full runs: about $3.

## Risks

- **Structured output across providers:** the draft schema uses a tagged union of figure types. If
  providers reject the schema, I'll flatten it (a single figure type with optional fields) and record
  that.
- **Strict validation may drop many good claims** (the M3 limits). The drop reasons per run show where,
  and loosening is a validator change with its own tests.
- **Routing too strict:** if `zdr` leaves no provider for a model, the call fails clearly. You decide
  whether to relax it; I won't relax it silently.
- **Variance:** one run per model is a single sample. Report it as such and don't over-read small
  differences.

## Options not taken

- A tool-calling agent loop: a fixed graph is simpler, cheaper and enough for v0 (A5, narrow job).
- A model as judge for answers: code checks figures and pages (A4).
- Long-context answering: already declined in M1.

## Proof

- `tests/test_agent.py` (fakes, offline), one test per behaviour:
  - scope rejects an unknown ticker and a 1,001-character question with no model call;
  - a good draft becomes an answered note with code-computed figures;
  - a draft that cites a wrong page is dropped;
  - a stated computed number is dropped;
  - more than half dropped triggers one re-draft, which lists the reasons;
  - unparseable output retries once, then errors;
  - `not_found` passes through;
  - run metadata carries tokens and retrieved pages.
- `tests/test_llm.py`: the chat request carries the schema and routing options; usage and cost are
  summed; a parse failure retries once.
- Measured: the Haiku and Sonnet baselines on 50 golden items, with all metrics above, tokens, cost
  and time.
- `make check`, `make typecheck`, `make test` and `make test-db` pass; the `verifier` checks the diff
  against this plan.
