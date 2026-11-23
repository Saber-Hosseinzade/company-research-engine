# Spec: push gate for feature branches

From: intent.md (Saber-Hosseinzade). Status: ready for review. Date: 2026-11-23.
Skills applied: security-policy, ai-design-standards.

By the author's decision, the intent, this spec, the plan and the code are reviewed and merged in one
PR (#1). The intent is therefore not yet merged on `main` when this spec is written.

## Requirements

- R1. `.claude/settings.json` has no `git push` deny rule. It has an `ask` rule `Bash(git push:*)` as
  a second layer. All other deny rules are unchanged.
- R2. The approval-gate hook treats a Bash command as a push when the text contains `git`, optionally
  followed by git global options (`-C <dir>`, `-c <key=value>`, `--<option>`), followed by `push`.
  This holds anywhere in the command: after `;`, `&&`, `|`, inside `bash -c` or `sh -c`, after
  `env`, or inside `$( )`. Quote characters (`'`, `"`) and backslashes are removed before matching.
- R3. A push is denied when any destination is `main`. The destination is the part after `:` in a
  refspec, or the whole refspec without `:`, after removing a leading `+` and a `refs/heads/` prefix.
  This includes deleting `main` (`:main`, `--delete main`).
- R4. A push is denied when it is forced or its destination can't be known from the text:
  - `-f`, or a short-option cluster containing `f` such as `-uf`;
  - `--force`, `--force-with-lease…` or `--force-if-includes`;
  - a refspec starting with `+`;
  - `--all` or `--mirror`;
  - a refspec containing `*`, `$`, a backtick, `{`, `}` or `~`. Bash expands these before git reads them,
    so the text doesn't show the destination. Braces and `~` were added after the second review.
- R5. A push with no refspec, or with refspec `HEAD`, resolves its destination from the repo the push
  runs in: the `-C <dir>` given, else the project directory.
  - The destination is the branch that `@{push}` names.
  - When `@{push}` doesn't resolve, for example because there is no upstream yet, the destination is
    the current branch.
  - The push is denied when the destination is `main`, or when there is no current branch (detached
    HEAD).
  - A bare push without `-C` is denied when the command also contains `cd` or `pushd`, because the
    repo it runs in can't be known. This was added after the second review.
- R6. Any other push asks the person running the session. The reason names the command. In
  non-interactive runs (CI, evals), "ask" is treated as deny.
- R7. `gh pr merge` is denied anywhere in a command, with the quote removal of R2. The reason says
  that a person merges pull requests on GitHub.
- R8. Every deny and ask gives a reason that says what to do instead.
- R9. Offline tests in `tests/` cover each requirement, with at least one case per bypass listed in
  the intent. Without `/usr/bin/jq` or `git` the tests fail with a clear message rather than skipping,
  because `CLAUDE.md` requires nothing skipped.
- R10. The hook's other rules (guardrail edits, commits on main, noreply email, dependencies, AWS) keep
  their current behaviour. The header comment lists the new rules.

## Design

Only plain code: a bash hook and its pytest tests. There is no agent and no model call (A4); the
decision is a fixed rule.

The hook runs before each Bash call. The push check works in this order:

1. Normalise: remove quotes and backslashes from the command.
2. Match R2. On no match, go on to the remaining rules. On a match, take the words after `push` up to
   the next `;`, `&`, `|` or `)`.
3. Sort the words into options and positional words. Apply R4 to the options.
4. The first positional word is the remote. The rest are refspecs. Apply R4 (`+`, `*`, `$`, backtick)
   and then R3 to each refspec.
5. With no refspec, or `HEAD`, apply R5. `@{push}` is read with
   `git -C <dir> rev-parse --abbrev-ref --symbolic-full-name @{push}`, then the remote prefix is removed.
6. Otherwise ask (R6).

The `gh pr merge` check (R7) runs on the same normalised text, before the push check.

The work continues on PR #1 (`chore/push-gate`), which already has R1 and part of R3, R4 and R6.
`plan.md` lists what is left.

## How each constraint in the intent is met

| Constraint | Met by |
| --- | --- |
| Every push still asks (`CLAUDE.md`) | R6; the deny cases never ask |
| A human approves the merge | R7, and the GitHub ruleset on `main` |
| Ruleset stays the server-side guard | Unchanged; concern 1 |
| Can't tell where a push goes → deny or ask, never allow | R2 normalisation, R4 (`$`, `*`, backtick), R5 (detached HEAD), R6 |
| Tests stay offline (A10) | R9: temporary local repos, no remote, no network |
| `docker push` and other deny rules unchanged | R1, R10 |
| No secrets in reasons or tests (S1) | Reasons quote only the command; tests use made-up branch names |

The rest of S2–S12 and A1–A3, A5–A9, A11–A12 don't apply: there are no settings, AWS, data, models
or dependencies.

## Evaluation

- Metric: share of known bypass commands that the hook denies. Target 100%, measured by `make test`.
- Each bypass found later gets a test before it is fixed (`/bugfix`).
- `make check`, `make typecheck` and `make test` pass. Changes to `.claude/` also run the agent evals
  in CI.
- Cost per run: none. There are no model calls.

## Open questions from the intent

- How does a bare push resolve its destination? Through `@{push}`, falling back to the current branch
  (R5).
- Keep the settings `ask` rule? Yes, as a second layer (R1). It only matches commands that start with
  `git push`, so the hook remains the main check.
- Skip or fail without `jq` or `git`? Fail with a clear message (R9).
- What happens to PR #1? It continues on this spec. There is no restart.

## Areas of concern

1. **The hook parses shell text, so it can't be complete.** Aliases, scripts, `eval`, variables set
   earlier in the command, and git config such as `remote.pushDefault` or `push.default=upstream` with
   unusual branch layouts can all hide where a push goes. R4 and R5 close the cases known today.
   The GitHub ruleset on `main` (pull request required, force push and deletion blocked) is the guard
   that holds regardless. This only holds while that ruleset stays active. Owner: repository owner.
2. **False denies.** Text that looks like a push to main, such as `git commit -m "git push origin main
   is blocked"`, is denied, because quotes are removed before matching. The same applies to a PR body
   passed with `--body`, a heredoc, or `echo` and `grep` of such text. PR bodies that describe this
   change are the likely trigger. The workaround is to pass the text in a file: `git commit -F <file>`,
   `gh pr create --body-file <file>`. This is accepted. Owner: repository owner.
3. **Merges through `gh api`.** `gh api …/pulls/N/merge -X PUT` merges without `gh pr merge`. Gating
   `gh api` writes is out of scope in the intent. The ruleset still requires a PR and its checks.
   Owner: repository owner, as a later intent if needed.
4. **The `main` ruleset has no required status check yet.** Until `pytest` is required, a PR can be
   merged with failing tests. Owner: repository owner, in the GitHub settings.
