# Agent evals

Regression tests for the **agent's configuration**: `CLAUDE.md`, `.claude/` (skills, hooks, settings)
and the model behind them. When one of those changes, the suite shows whether Claude still does the work
to the same standard.

These are not the project's own model evals (answer quality of the app); those belong to each project.

## How it works

Each `*.json` file is one task:

| Field | Meaning |
| --- | --- |
| `name`, `source` | What the eval is and where it came from (a merged PR, an incident, a policy rule) |
| `from` | Commit the task starts from; `HEAD` for tasks that work on the current code |
| `prompt` | The task, as a person would ask it |
| `may_change` | Folders and files the run may touch; anything else fails the eval |
| `checks` | Commands that define an acceptable result; every one must exit 0 |
| `allowed_tools` | Optional; defaults to read, edit and `make test/lint/typecheck` |

`run.sh` runs each eval in a scratch worktree with this branch's configuration, using Claude Code
non-interactively, and `check.sh` grades what it left behind.

## Run

- Locally: `make evals` (uses your Claude login; costs tokens).
- CI: `.github/workflows/agent-evals.yml` on pull requests that change `CLAUDE.md`, `.claude/` or
  `evals/`, and on demand. It needs the repository secret `CLAUDE_CODE_OAUTH_TOKEN` (your Claude subscription, from
  `claude setup-token`) or `ANTHROPIC_API_KEY`; without either, the job is
  skipped with a notice.

## Grow the suite

Aim for 20 to 50 evals from real work: each merged change worth protecting, and every incident or
mistake Claude made twice, becomes one. Drop evals that every configuration passes.

## Measure

- Pass rate per run, over time.
- Time from an incident or repeated mistake to its eval.
