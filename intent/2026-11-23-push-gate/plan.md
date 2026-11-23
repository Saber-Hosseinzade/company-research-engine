# Plan: push gate for feature branches

From: intent.md and spec.md. Accepted by: Saber-Hosseinzade. Date: 2026-11-23.

## Files that change

- `.claude/hooks/approval-gate.sh`: rewrite the push rule and add the `gh pr merge` rule.
- `tests/test_approval_gate.py`: new cases and helper fixtures.
- `.claude/settings.json`: already done in 3044132 (R1). No further change.

## Order of work

1. **Tests first.** Add failing cases for every requirement not yet met, and commit them on their own:
   - the bypasses from the intent: `bash -c`, quoted `main`, wildcard refspec, bare push with
     upstream `main` (R2–R5);
   - `$` and backtick refspecs, `--delete main`, `-C <dir>` branch lookup, detached HEAD (R3–R5);
   - `gh pr merge`, plain and inside `bash -c` (R7);
   - every deny or ask returns a non-empty reason (R8);
   - a commit on a feature branch and an AWS command with another region keep their current decision
     (R10).

   The fixtures build a temporary repo on a chosen branch, with a local bare repo as `origin`, so
   upstreams and `@{push}` resolve without network access (R9, A10). A fixture fails with a clear
   message when `/usr/bin/jq` or `git` is missing (R9).
2. **Normalise and match** (R2): remove `'`, `"` and `\`; find `git [global options] push` anywhere in
   the text; take the arguments up to `;`, `&`, `|` or `)`.
3. **Options and refspecs** (R3, R4): deny force flags, `--all` and `--mirror`; deny refspecs with `+`,
   `*`, `$` or a backtick; compute the destination and deny `main`, including `--delete main`.
4. **Bare push and `HEAD`** (R5): read `-C <dir>` if given; resolve `@{push}`, falling back to the current
   branch; deny `main` or a detached HEAD.
5. **`gh pr merge`** (R7), checked before the push rule on the normalised text.
6. **Reasons and header** (R6, R8, R10): each decision says what to do instead; update the header
   comment.
7. Run `make check`, `make typecheck` and `make test`, and run the `verifier` subagent. Push after
   asking, and update the PR #1 description.

## Risks

- **Riskiest: removing quotes (step 2).** It can make ordinary commands look like pushes. A commit
  message mentioning `git push origin main` gets the commit denied (spec concern 2). The R10 tests
  check that common commands still get their usual decision.
- **`@{push}` depends on git config.** Unusual `push.default` or `remote.pushDefault` setups can resolve
  differently than expected (spec concern 1). The fallback denies whenever no current branch exists.
- **A broken regex blocks every Bash call.** The hook runs before each one. The full test run in step 7
  catches this before the push.

## Options not taken

- **Parse the command with Python `shlex`.** It handles quoting properly, but adds a Python start-up on
  every Bash call and a second language for one hook. Removing quotes is enough to reach the known
  cases, and the ruleset covers the rest.
- **Deny every push and let the person push by hand.** That was the old state, which the intent
  replaces.
- **Ask, never deny, for unclear destinations.** Rejected because the intent asks for deny when a push
  might reach `main`, and because a person approving many pushes stops reading them.

## Proof

| Requirement | Test in `tests/test_approval_gate.py` |
| --- | --- |
| R1 | `settings.json` has no `git push` deny and has the `ask` rule (a new settings test) |
| R2 | Pushes inside `bash -c`, after `env`, after `&&`, after `-C` and `-c` options are detected |
| R3 | `main` as a plain name, quoted, `x:main`, `HEAD:main`, `refs/heads/main`, `:main` and `--delete main` are denied |
| R4 | `-f`, `-uf`, `--force*`, `+ref`, `--all`, `--mirror`, `*`, `$B` and backtick refspecs are denied |
| R5 | A bare push with upstream `main`, a bare push on `main`, and detached HEAD are denied; a bare push on a feature branch asks |
| R6 | Feature-branch pushes ask |
| R7 | `gh pr merge 1` and `bash -c 'gh pr merge 1'` are denied |
| R8 | Every deny and ask returns a non-empty reason |
| R9 | All of the above run offline; a missing `jq` fails the run |
| R10 | Commit on `main` is still denied, `uv add` still asks, and region `us-east-1` is still denied |
