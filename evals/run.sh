#!/usr/bin/env bash
# Runs every eval in evals/*.json against this branch's CLAUDE.md and .claude/ configuration.
# Each eval runs Claude Code non-interactively in a scratch worktree and is graded by evals/check.sh.
# Needs `claude` logged in (locally) or ANTHROPIC_API_KEY (CI). Every run costs tokens.
set -uo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root" || exit 1
work=$(mktemp -d)
cp -r evals "$work/evals" # grade from a copy made before any run
default_tools="Read,Edit,Write,Glob,Grep,Bash(make test),Bash(make lint),Bash(make typecheck)"
failed=0

for eval in "$work"/evals/*.json; do
  run="$work/run"
  git worktree add -q --detach "$run" "$(jq -r '.from // "HEAD"' "$eval")" # the code before the task
  rm -rf "$run/evals" "$run/.claude"                                       # keep the checks out of the run
  cp -r CLAUDE.md .claude "$run/"                                          # the configuration under test
  rm -rf "$run/.claude/settings.local.json" "$run/.claude/worktrees" "$run/.claude/tests-locked"
  # Scratch commit in a throwaway worktree: hooks are skipped here only.
  git -C "$run" add -A && git -C "$run" commit -q --no-verify --allow-empty -m "configuration under test"
  (
    cd "$run" || exit 1
    export UV_PROJECT_ENVIRONMENT="$run/.venv" # own venv, so the main one is untouched
    uv sync -q --frozen
    # --permission-mode default: the repo defaults to plan mode, which would block every edit.
    claude -p "$(jq -r '.prompt' "$eval")" \
      --permission-mode default \
      --allowedTools "$(jq -r --arg d "$default_tools" '.allowed_tools // $d' "$eval")" \
      --output-format json </dev/null >"$work/result.json"
    "$work/evals/check.sh" "$eval" "$work/result.json"
  ) || failed=$((failed + 1))
  git worktree remove --force "$run"
done

rm -rf "$work"
echo "$failed eval(s) failed"
[ "$failed" -eq 0 ]
