# CLAUDE.md

Context for AI coding assistants.

- Project record (local, git-ignored; created from `PROJECT_CONTEXT.template.md`): @PROJECT_CONTEXT.md
- Private notes for this project go in `CLAUDE.local.md` (git-ignored). Create it when there is
  something private to note, such as a client name or a dataset's licence terms.

## What this is

A Python 3.12 project for AI agents, RAG or document AI on AWS. Code in `src/app/`, offline tests in
`tests/`, local Postgres + pgvector in `compose.yaml` (`make db-up`).

## Verifying your work

- `make check`: every pre-commit hook "Passed" or "Skipped"
- `make typecheck`: "Success: no issues found"
- `make test`: "N passed", nothing failed or skipped

Run all three before reporting any task or commit done, and paste the output. If a test fails, fix the
code, not the test; never skip or delete one. Tests stay offline. `make help` lists other commands.

## Design rules

- **Model is config, not code:** `MODEL_PROVIDER` (`bedrock` | `openrouter`) and `MODEL_ID`.
- **Deterministic work stays in code;** use a model call only where judgement is needed.
- **Dependencies:** `uv add <pkg>` (`--dev` for tools); commit `pyproject.toml` and `uv.lock` together.
  Add only what the project needs.
- **No keys in code or repo:** local values in `.env.local`; `.env.example` lists every setting, empty.
- **Data** lives in `data/` and is never committed; ship a script that fetches it.

## Environment

AWS profile `sandbox`, region `eu-central-1`, both set by the dev container; log in with
`make sso-login`. Use no other profile or region. Docker runs inside the dev container.
Ask before the first call to a new Bedrock model: for third-party models it creates an AWS Marketplace
subscription with its own charges.

## Development lifecycle

- **Plan:** non-trivial changes start as `intent/<date>-<name>/intent.md` (`/intent`, see
  `intent/README.md`) and need a merged intent before design or code. Small fixes don't.
- **Design:** an accepted intent becomes `spec.md` (`/spec`), applying the `security-policy` and
  `ai-design-standards` skills. Build starts from a merged spec.
- **Build:** sessions start in plan mode; the accepted plan is committed as `plan.md`, citing requirement
  IDs, and updated in the same commit when the code departs from it. Run the `verifier` subagent before
  reporting done.
- **Test:** bugs are fixed test-first (`/bugfix`, tests locked during the fix). Changes to `CLAUDE.md`
  or `.claude/` are regression-tested by `make evals`.
- **Deploy:** PRs are reviewed against `REVIEW.md` (`/pr-review`, `claude-review` in CI); a human
  approves the merge. `.claude/hooks/approval-gate.sh` enforces the gates.
- **Maintain:** `maintain.yml` turns a control-band breach of the CI failure rate into an intent PR.
  Every fixed incident gets an eval.

## Working rules

- One branch per change, merged to `main` by PR. Commit with a GitHub noreply address.
- Don't skip pre-commit hooks with `--no-verify`; fix the finding.
- Never push, deploy or destroy cloud resources without asking. Don't add features beyond the agreed
  step without asking.
- Docs describe what the project does and how to use it. No personal details (people, employers,
  machines, account IDs) in tracked files; private notes go in the git-ignored local files.
- Keep `PROJECT_CONTEXT.md` current when a step finishes, a decision is made or a session
  ends: date, status, next step. Write it to be read without the code or the chat.
- Say what was measured and what was assumed. PR descriptions state what changed and how it was
  measured; add each measured result, dated, to Results in `PROJECT_CONTEXT.md`. Docs claim only
  measured results.
- When Claude makes the same mistake twice, add the correction under "Things Claude gets wrong".

## Things Claude gets wrong

- (none yet)
