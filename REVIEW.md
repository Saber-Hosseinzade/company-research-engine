# Review instructions

How Claude reviews a pull request in this repo, locally (`/pr-review`) or in CI. Findings inform the
human reviewer; they never approve or block a PR on their own.

## Passes

Run four passes and tag each finding with its pass:

- **Bugs:** logic errors, broken edge cases, unhandled failures (model timeouts, empty retrieval), async
  mistakes, subtle regressions.
- **Security:** the rules in `.claude/skills/security-policy/SKILL.md` (cite S1–S12): secrets, AWS
  profile and region, IAM scope, personal data in logs, untrusted model output, new dependencies.
- **Compliance:** the change matches its `intent/*/spec.md` and `plan.md` (cite R-numbers and plan
  steps), and `.claude/skills/ai-design-standards/SKILL.md` (cite A1–A12). Flag code the plan does not
  mention and plan steps with no code.
- **Tests:** every requirement the change delivers has a test that would fail without it; no test was
  weakened (removed asserts, added skips); tests stay offline.

Also flag when the change makes `CLAUDE.md`, `README.md` or `.env.example` out of date.

## Severity

- **Important:** would break behaviour, leak data or secrets, breach a policy rule, or contradict the
  spec or plan.
- **Nit:** style, naming, small clarity improvements.

## Output

A table, most severe first: Severity | File:Line | Pass: issue. Then one line per area with no findings.
Report at most five nits; summarise the rest as a count.

## Do not report

Formatting, import order and type errors (ruff, mypy and pre-commit run in CI), `uv.lock` contents, and
anything outside the diff unless the diff breaks it.

## Learning loop

When a review flags the same mistake a second time, propose the correction for `CLAUDE.md` under
"Things Claude gets wrong" in the same review.
