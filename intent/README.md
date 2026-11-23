# Intent and spec

Every non-trivial change starts here as a short `intent.md`: what is wanted, why, and under which
constraints. Once accepted, it becomes a `spec.md`: numbered requirements, a design, and flagged concerns.
Both are written before any code, and the code derived from them lives in the same repo.

## Layout

One folder per change, named `YYYY-MM-DD-short-name/`; the spec sits next to its intent.

```text
intent/
  _template.md          intent template
  _spec-template.md     spec template
  _plan-template.md     plan template
  2026-11-23-example-change/
    intent.md
    spec.md
    plan.md
```

## Plan: intent

1. Describe the problem to Claude in your own words (`/intent`, or ask for a new intent).
2. Claude asks the questions an analyst would: scope, users, constraints, what success looks like.
3. Claude writes `intent.md` from `_template.md`, with `Status: draft`.
4. Correct anything Claude misunderstood.
5. Commit it on a branch `intent/<short-name>` and open a pull request.
   **Merged = accepted** into design; **closed = rejected**. The Git history keeps author and dates.

## Design: spec

1. Ask for a spec of the accepted intent (`/spec`).
2. Claude reads the intent and the code, applies the policy skills (`security-policy`,
   `ai-design-standards`), and writes `spec.md` from `_spec-template.md`.
3. Work through the **areas of concern** first: each names a rule that can't be met, a conflict, or an
   unmeasured risk, and who decides it.
4. Commit on a branch `spec/<short-name>`. **Merged = accepted** into build.

## Build: plan, then code

1. Start Claude Code (plan mode is the default) and ask for an implementation plan from the spec:
   files that change, order of work, risks, and the tests that prove each requirement.
2. Question it: what could break, which step is riskiest, what options were dropped. Iterate until
   someone who never saw the conversation could implement it from the plan alone.
3. Commit the accepted plan as `plan.md` on the change's branch, then let Claude implement.
4. If the code departs from the plan, update `plan.md` in the same commit.
5. The `verifier` subagent checks the diff against the plan before the PR.

Independent tasks can run in parallel sessions, each in its own worktree:
`claude --worktree <task-name>`. Start with two or three; add more only while review keeps up.

## Measure

- Time from the first conversation to the committed `intent.md` (Git history).
- Share of intents merged rather than closed.
- Edits to an `intent.md` after its `spec.md` was first committed: fewer means clearer intent.
- Time from the `intent.md` commit to the `spec.md` commit.
- Edits to a `spec.md` after build starts: requirements rework.
- Share of changes merged after the first implementation pass; how often the merged diff matches
  `plan.md`.
