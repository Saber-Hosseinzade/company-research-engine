# Intent: push gate for feature branches

Author: Saber-Hosseinzade. Status: draft. Date: 2026-11-23.

## Problem

`.claude/settings.json` denies every `git push`, so Claude cannot push any branch. Each push and PR has
to be done by hand, even for branches whose work Claude has finished and checked. A deny rule can't be
overridden by local settings, so the only way to allow pushes is to change the guardrail itself.

A first version is already open as PR #1 (`chore/push-gate`), written before this intent. Review found
pushes that the hook either lets through or only asks about:

- a push wrapped in another shell (`bash -c 'git push origin main'`): allowed, with no prompt;
- a quoted branch name (`git push origin "main"`): asks, when it should deny;
- a wildcard refspec (`refs/heads/*:refs/heads/*`): asks, when it should deny;
- a bare `git push` on a branch whose upstream is `main`: not yet tested.

Merging is also ungated: nothing stops Claude from running `gh pr merge`, although `CLAUDE.md` says a
human approves the merge.

## Proposed outcome

- Claude can push a feature branch after the person running the session approves that push.
- Claude can never push to `main`, force-push, or push with `--all` or `--mirror`. These are denied, not
  asked.
- Claude can open pull requests but cannot merge them: `gh pr merge` is denied.
- Success: every bypass found in review, and each bypass found later, has an offline test that expects
  deny. There are no known commands that reach `main` and are allowed or only asked about. Measured by
  `make test`.

## Affected users and systems

- The person running Claude Code sessions in this repo, who approves each push.
- `.claude/settings.json`, `.claude/hooks/approval-gate.sh`, and the hook's tests in `tests/`.
- CI runs, where an "ask" is treated as deny, so CI can't push.
- GitHub: the repository and its `main` ruleset. No AWS resources.

## Constraints

- `CLAUDE.md`: "Never push, deploy or destroy cloud resources without asking", and a human approves the
  merge. Every push still asks.
- The hook only sees commands Claude runs. The GitHub ruleset on `main` (pull request required, force
  pushes and deletion blocked) stays the server-side guard and isn't weakened.
- When the hook can't tell where a push goes, it must deny or ask, never allow.
- Tests stay offline.
- `docker push` and the other deny rules stay unchanged.

## Out of scope

- Pushing without asking.
- Gating other `gh` writes (`gh api`, `gh repo edit`, ruleset changes) beyond `gh pr merge`.
- Deleting remote branches as a workflow.
- Commands that hide a push by indirection the hook can't parse, such as scripts, aliases or `eval`.
  The ruleset covers these.

## Open questions

- How should a bare `git push` resolve its destination: `@{push}`, the upstream, or `push.default`?
  And what happens when the branch has no upstream yet?
- Should the settings file keep an `ask` rule for `git push` as a second layer, given that it only
  matches commands that start with `git push`?
- Should the hook tests skip or fail when `jq` or `git` is missing?
- Does PR #1 continue on this intent, or close and restart from the spec?
