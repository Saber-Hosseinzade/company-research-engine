---
name: pr-review
description: Review the current branch's changes against REVIEW.md - bugs, security policy, compliance with spec.md and plan.md, and tests. Use when the user asks to review a branch or PR before merging.
---

# Review a branch against REVIEW.md

1. Read `REVIEW.md`, `.claude/skills/security-policy/SKILL.md` and
   `.claude/skills/ai-design-standards/SKILL.md`.
2. Get the change: `git diff main...HEAD` and `git log --oneline main..HEAD`. If the branch touches an
   `intent/*/` folder, or the commits name one, read its `spec.md` and `plan.md`.
3. Run the four passes from `REVIEW.md`. Read surrounding code where the diff alone is not enough.
   Verify each finding before reporting it; drop what you cannot point to a line for.
4. Report in the `REVIEW.md` format. Do not edit any file; the author decides what to fix.
