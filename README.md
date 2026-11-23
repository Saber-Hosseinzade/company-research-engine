# company-research-engine

Given a listed company and a question, returns a research note in which every claim and figure cites a
page of a public filing. Citations and arithmetic are checked in code, never by the model.

> **Status:** planning. Nothing is built yet. The first version (v0) is a single filings agent that
> answers questions over SEC 10-K filings with validated citations, run locally.

## What it is not

Analysis only. It gives no buy, sell or hold recommendations, no credit ratings, no lending decisions and
no investment advice. Outputs are analysis, not advice, and models can be wrong.

## Data

Public filings and public company releases only, starting with SEC EDGAR. Filings are fetched by a
script into `data/` and are not committed.

## Run locally

Open the folder in the dev container (VS Code: *Reopen in Container*), then:

```bash
cp .env.example .env.local   # fill in what you need
make test                    # offline tests
make db-up                   # Postgres + pgvector, if the project uses it
```

The model is configuration: set `MODEL_PROVIDER` (`bedrock` or `openrouter`) and `MODEL_ID` in `.env.local`.

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
