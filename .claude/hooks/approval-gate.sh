#!/usr/bin/env bash
# PreToolUse approval gates. Each rule allows (no output), asks the person running the session,
# or denies with the route to approval. Non-interactive runs (CI, evals) treat "ask" as deny.
#   - Edits to the agent's own guardrails and CI: ask.
#   - git commit/merge/rebase on main: deny (one branch per change).
#   - git push to main, forced, --all/--mirror, or to a destination that can't be read
#     (wildcard, variable, brace, tilde, detached HEAD, bare push after cd): deny; any other
#     push: ask. Quoted or wrapped pushes (bash -c, sh -c, $( )) count. A bare push is resolved
#     through @{push}.
#   - gh pr merge: deny (a person merges pull requests).
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

    # Push and merge rules read the command with quotes and backslashes removed, so a push quoted or
    # wrapped in bash -c, sh -c or $( ) is still seen (intent/2026-11-23-push-gate/spec.md R2).
    flat=${cmd//$'\\\n'/ } # a line continuation joins two lines
    flat=${flat//$'\n'/;}  # any other newline separates commands
    flat=${flat//[\'\"\\]/}
    to_main="Pushes to main are not allowed; main changes only by a reviewed pull request. Push a feature branch and open a PR."

    # Claude opens pull requests but never merges them (R7).
    if [[ "$flat" =~ (^|[^[:alnum:]_-])gh[[:space:]]+pr[[:space:]]+merge([[:space:]]|$) ]]; then
      decide deny "Claude does not merge pull requests; a person merges them on GitHub. Ask the user to review and merge the PR."
    fi

    # Pushes: never to main, forced or to an unreadable destination; any other push asks (R2-R6).
    # Every push in the command is checked, including git called by path (/usr/bin/git).
    push_re='(^|[^[:alnum:]_.-])git(([[:space:]]+(-[Cc][[:space:]]+[^[:space:];&|()]+|--?[^[:space:];&|()]+([[:space:]]+[^-[:space:];&|()][^[:space:];&|()]*)?))*)[[:space:]]+push([[:space:]]+[^;&|()]*)?([;&|()]|$)'
    cd_re='(^|[;&|([:space:]])(cd|pushd)([[:space:]]|$)' # a directory change hides which repo a bare push uses
    unreadable='[*$`{}~]'                                # wildcards, variables, commands, braces, ~ (R4)
    rest=$flat
    found=""
    while [[ "$rest" =~ $push_re ]]; do
      found=1
      rest=${rest#*"${BASH_REMATCH[0]}"} # continue after this push
      opts=${BASH_REMATCH[2]}
      args=${BASH_REMATCH[6]}
      dir=$root # the repo the push runs in: -C <dir> if given (R5)
      has_c=""
      if [[ "$opts" =~ -C[[:space:]]+([^[:space:]]+) ]]; then
        has_c=1
        dir=${BASH_REMATCH[1]}
        [[ "$dir" == /* ]] || dir=$root/$dir
      fi

      read -ra words <<<"$args"
      positional=()
      for w in "${words[@]}"; do
        case "$w" in
          --all | --mirror | --force | --force-with-lease* | --force-if-includes)
            decide deny "git push $w can change main or rewrite history. Push one feature branch by name instead."
            ;;
          --*) ;;
          -*f*) decide deny "Force pushes are not allowed. Push without -f." ;;
          -*) ;;
          *) positional+=("$w") ;;
        esac
      done

      bare=""
      refspecs=("${positional[@]:1}") # the first positional word is the remote
      [ ${#refspecs[@]} -eq 0 ] && bare=1
      for spec in "${refspecs[@]}"; do
        [[ "$spec" == +* ]] && decide deny "Force pushes ($spec) are not allowed. Push without the leading +."
        if [[ "$spec" =~ $unreadable ]]; then
          decide deny "The push destination ($spec) can't be checked: it uses a wildcard, variable, brace, ~ or command. Name the feature branch."
        fi
        dst=${spec#*:}
        dst=${dst#refs/}
        dst=${dst#heads/}
        [ "$dst" = "main" ] && decide deny "$to_main"
        [ "$dst" = "HEAD" ] && bare=1
      done

      # A bare push (or HEAD) goes where @{push} says, else to the current branch (R5).
      if [ -n "$bare" ]; then
        if [ -z "$has_c" ] && [[ "$flat" =~ $cd_re ]]; then
          decide deny "A bare push after cd can't be checked: the hook can't tell which repo it runs in. Name the branch (git push origin <branch>) or use git -C <dir> push."
        fi
        if ! branch=$(git -C "$dir" symbolic-ref --quiet --short HEAD 2>/dev/null); then
          decide deny "The current branch can't be read (detached HEAD or not a repo), so the push destination is unknown. Switch to a feature branch and name it in the push."
        fi
        if target=$(git -C "$dir" rev-parse --abbrev-ref --symbolic-full-name '@{push}' 2>/dev/null); then
          dst=${target#*/}
        else
          dst=$branch
        fi
        [ "$dst" = "main" ] && decide deny "$to_main"
      fi
    done
    [ -n "$found" ] && decide ask "This pushes to GitHub: $cmd. Approve only if you expect this push."

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
