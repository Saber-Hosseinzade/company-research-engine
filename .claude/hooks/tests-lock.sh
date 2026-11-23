#!/usr/bin/env bash
# PreToolUse hook: while .claude/tests-locked exists (set during a bug fix, after the failing test is
# committed), block edits to tests/ so the fix changes the code, not the check. `make tests-unlock` ends it.
set -uo pipefail

[ -f "$CLAUDE_PROJECT_DIR/.claude/tests-locked" ] || exit 0
file=$(jq -r '.tool_input.file_path // empty')
case "$file" in
  "$CLAUDE_PROJECT_DIR"/tests/* | tests/*)
    echo "Tests are locked during this fix (.claude/tests-locked): fix the code, not the test." >&2
    exit 2
    ;;
esac
exit 0
