#!/usr/bin/env bash
# PostToolUse hook: after Claude edits a Python file, format it and fix safe lint findings.
# Anything ruff cannot fix is sent back to Claude (exit 2) so it corrects the line now,
# instead of the finding piling up until the commit hook or CI.
set -uo pipefail

file=$(jq -r '.tool_input.file_path // empty')
case "$file" in
  *.py | *.pyi) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

cd "$CLAUDE_PROJECT_DIR" || exit 0
# Fix first, format last, so removed imports leave no stray blank lines.
out=$(uv run --frozen -q ruff check --fix -q "$file" 2>&1)
status=$?
uv run --frozen -q ruff format -q "$file"
if [ "$status" -ne 0 ]; then
  echo "ruff found problems it cannot fix in $file:" >&2
  echo "$out" >&2
  exit 2
fi
