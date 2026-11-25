# company-research-engine

Given a listed company and a question, returns a research note in which every claim and figure cites a
page of a public filing. Citations and arithmetic are checked in code, never by the model.

> **Status:** v0, local only. A single filings agent answers questions about NVDA, AMD, META, GOOG and
> AMZN from their 10-K and 10-K/A filings since 2024, with citations checked in code. On a 50-question
> golden set it accepted 0.850 and 0.900 of answerable questions over two runs (Haiku 5.5). The API has
> no authentication and binds to localhost only.

## What it is not

Analysis only. It gives no buy, sell or hold recommendations, no credit ratings, no lending decisions and
no investment advice. Outputs are analysis, not advice, and models can be wrong.

## Data

Public filings and public company releases only, starting with SEC EDGAR. Filings are fetched by a
script into `data/` and are not committed.

## Run locally

Open the folder in the dev container (VS Code: *Reopen in Container*), then:

```bash
cp .env.example .env.local   # MODEL_PROVIDER=openrouter, MODEL_ID, OPENROUTER_API_KEY,
                             # EMBEDDING_MODEL_ID, SEC_USER_AGENT
make test                    # offline tests
make db-up                   # Postgres + pgvector
make fetch ingest chunk embed  # download filings, load pages, build and embed chunks
make up                      # build and start the app: http://127.0.0.1:8000
```

The page at `/` takes a company and a question and shows the note; each citation opens the stored filing
page with the quoted text highlighted and a link to the original on EDGAR. The API is
`POST /research {"ticker": "NVDA", "question": "..."}`, which returns the note as JSON (`POST /invocations`
is the same call under the name AgentCore Runtime uses). `make serve` runs the same app outside Docker
with reload. Local Postgres is PostgreSQL 18 with pgvector.

The model is configuration: `MODEL_ID` in `.env.local` (v0 builds only `MODEL_PROVIDER=openrouter`).

## Deploy to AgentCore Runtime (in progress)

`infra/` holds a CDK stack that runs the same image on Amazon Bedrock AgentCore Runtime, with the
filings in Aurora PostgreSQL Serverless v2 reached through the RDS Data API. It has not been deployed
yet. Steps and teardown: [infra/README.md](infra/README.md).

## Development

| Command | What it does |
| --- | --- |
| `make test` / `make cov` | Offline tests / with coverage |
| `make lint` / `make fmt` | Ruff check / fix |
| `make typecheck` | mypy |
| `make check` | All pre-commit hooks on all files (what CI runs) |
| `make help` | All targets |

Add dependencies with `uv add <package>`.

## License

MIT
