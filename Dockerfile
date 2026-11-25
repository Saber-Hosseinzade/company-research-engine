# The API (app.api) for local Compose and AgentCore Runtime (ARM64, port 8080: AgentCore's contract).
# Ingest, embed and fetch run from the dev container; edgartools is not in the image (spec R2).
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first (cached layer), then the app itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project
COPY src ./src
RUN uv sync --frozen --no-default-groups

RUN useradd --system --uid 10001 app
USER app

EXPOSE 8080
# No access log: page URLs carry excerpt text (spec R17).
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
