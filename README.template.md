<!-- Rename to README.md in a new project and replace the placeholders. -->

# Project name

One or two sentences: what this project does and for whom.

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
