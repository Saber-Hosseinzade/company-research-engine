#!/usr/bin/env bash
# PreToolUse approval gates. Each rule allows (no output), asks the person running the session,
# or denies with the route to approval. Non-interactive runs (CI, evals) treat "ask" as deny.
#   - Edits to the agent's own guardrails and CI: ask.
#   - git commit/merge/rebase on main: deny (one branch per change).
#   - git commit without a GitHub noreply user.email: deny.
#   - New or removed dependencies: ask (security-policy S12).
#   - AWS: any profile other than sandbox or region other than eu-central-1: deny;
#     commands that change resources: ask.
jq=/usr/bin/jq # absolute path, so a jq earlier on PATH is never used

fail() { # exit code 2 blocks the call
  echo "Approval gate: could not read the tool call, so it is blocked. Check .claude/hooks/approval-gate.sh." >&2
  exit 2
}
decide() { # decide <ask|deny> <reason>
  # shellcheck disable=SC2016 # $d and $r are jq variables, not shell ones
  "$jq" -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $r}}' || fail
  exit 0
}

input=$(cat)
tool=$("$jq" -r '.tool_name' <<<"$input") || fail
root=${CLAUDE_PROJECT_DIR:-$PWD}

case "$tool" in
  Edit | Write | MultiEdit)
    file=$("$jq" -r '.tool_input.file_path // empty' <<<"$input") || fail
    rel=${file#"$root"/}
    case "$rel" in
      .claude/settings.json | .claude/hooks/* | .github/workflows/* | .pre-commit-config.yaml | .devcontainer/* | maintain/bands.toml)
        decide ask "This edits a guardrail or CI file ($rel). Approve only if you asked for this change."
        ;;
    esac
    ;;

  Bash)
    cmd=$("$jq" -r '.tool_input.command // empty' <<<"$input") || fail

    # One branch per change: no commits on main.
    if [[ "$cmd" =~ (^|[;&|[:space:]])git[[:space:]]+(commit|merge|rebase|cherry-pick)([[:space:]]|$) ]] &&
      ! [[ "$cmd" =~ git[[:space:]]+(switch[[:space:]]+-c|checkout[[:space:]]+-b) ]] &&
      [ "$(git -C "$root" branch --show-current 2>/dev/null)" = "main" ]; then
      decide deny "Commits go on a branch, not main (CLAUDE.md: one branch per change). Run git switch -c <name> first."
    fi

    # Public history: commits use a GitHub noreply address, never a personal one.
    if [[ "$cmd" =~ (^|[;&|[:space:]])git[[:space:]]+commit([[:space:]]|$) ]]; then
      email=$(git -C "$root" config user.email 2>/dev/null || true)
      if [[ "$email" != *@users.noreply.github.com ]]; then
        decide deny "Commits must use a GitHub noreply address (now: ${email:-unset}). Run git config user.email '<id>+<username>@users.noreply.github.com' first."
      fi
    fi

    # Dependencies: check against security-policy S12 before adding.
    if [[ "$cmd" =~ (^|[;&|[:space:]])(uv[[:space:]]+(add|remove)|uv[[:space:]]+pip[[:space:]]+install|pip3?[[:space:]]+install)([[:space:]]|$) ]]; then
      decide ask "This changes the project's dependencies. Check the package against security-policy S12 (maintained, public source) before approving."
    fi

    # AWS: sandbox profile and eu-central-1 only; resource changes need a person.
    if [[ "$cmd" =~ (^|[;&|[:space:]])(aws|cdk|sam|terraform|agentcore)[[:space:]] ]]; then
      if [[ "$cmd" =~ --profile[=[:space:]]+([^[:space:]]+) ]] && [ "${BASH_REMATCH[1]}" != "sandbox" ]; then
        decide deny "AWS profile ${BASH_REMATCH[1]} is not allowed; use the sandbox profile only."
      fi
      if [[ "$cmd" =~ AWS_PROFILE=([^[:space:]]+) ]] && [ "${BASH_REMATCH[1]}" != "sandbox" ]; then
        decide deny "AWS profile ${BASH_REMATCH[1]} is not allowed; use the sandbox profile only."
      fi
      if [[ "$cmd" =~ (--region[=[:space:]]+|AWS(_DEFAULT)?_REGION=)([^[:space:]]+) ]] && [ "${BASH_REMATCH[3]}" != "eu-central-1" ]; then
        decide deny "Region ${BASH_REMATCH[3]} is not allowed; use eu-central-1 only."
      fi
      if [[ "$cmd" =~ aws[[:space:]]+s3[[:space:]]+(cp|mv|rm|sync|mb|rb)[[:space:]] ]] ||
        [[ "$cmd" =~ aws[[:space:]]+[a-z0-9-]+[[:space:]]+(create|delete|put|update|terminate|run|start|stop|modify|attach|detach|remove|deploy|invoke)[a-z0-9-]* ]] ||
        [[ "$cmd" =~ (cdk|sam|terraform|agentcore)[[:space:]]+(deploy|destroy|apply|launch|delete) ]]; then
        decide ask "This changes AWS resources in the sandbox account. Approve only if you expect this change."
      fi
    fi
    ;;
esac
exit 0
