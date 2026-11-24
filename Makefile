# Shortcuts for common commands. Run `make help` to list them.

PROFILE ?= sandbox

.PHONY: help sso-login sso-whoami install hooks test cov lint fmt typecheck check evals tests-unlock db-up db-down fetch m0-report pages golden-check

help:  ## List the targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

sso-login:  ## Log in to AWS SSO (sandbox profile, device code)
	aws sso login --profile $(PROFILE) --use-device-code

sso-whoami:  ## Show the identity the sandbox profile resolves to
	aws sts get-caller-identity --profile $(PROFILE)

install:  ## Install exactly what uv.lock pins
	uv sync --frozen

hooks:  ## Install the git pre-commit hooks
	uv run pre-commit install

test:  ## Run the offline tests
	uv run pytest -q

cov:  ## Run the tests with a coverage report
	uv run pytest -q --cov --cov-report=term-missing

lint:  ## Check style and common bugs
	uv run ruff check . && uv run ruff format --check .

fmt:  ## Fix formatting and safe lint findings
	uv run ruff format . && uv run ruff check --fix .

typecheck:  ## Check types with mypy
	uv run mypy

check:  ## Run every pre-commit hook on all files (what CI runs)
	uv run pre-commit run --all-files

evals:  ## Run the agent eval suite (uses Claude; costs tokens)
	evals/run.sh

tests-unlock:  ## End a bug fix: allow Claude to edit tests/ again
	rm -f .claude/tests-locked

db-up:  ## Start Postgres + pgvector in Docker
	docker compose up -d db

db-down:  ## Stop Postgres (data kept in the volume)
	docker compose down

fetch:  ## Download the 10-K and 10-K/A filings in scope from EDGAR into data/
	uv run python -m app.edgar

m0-report:  ## M0 spike: page and section stats for the fetched filings
	uv run python -m app.m0_report

pages:  ## Write every fetched page as text under data/pages/ (for reading and search)
	uv run python -m app.pages

golden-check:  ## Check that every golden quote and figure is on its expected page
	uv run python -m app.golden
