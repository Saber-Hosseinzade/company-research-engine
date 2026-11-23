---
name: spec
description: Turn an accepted intent/<date>-<name>/intent.md into a requirements and design spec.md, applying the repo's policy skills and flagging areas of concern. Use when the user asks for a spec or design for an accepted intent.
---

# Write a spec from an accepted intent

1. **Find the intent.** Use the folder the user names, or the most recent `intent/*/intent.md` without a
   `spec.md`. It must be merged on `main` (merged = accepted); if not, say so and stop.
2. **Load the policies.** Read `.claude/skills/security-policy/SKILL.md` and
   `.claude/skills/ai-design-standards/SKILL.md`. They are constraints, not suggestions.
3. **Read the code** the change touches, so the design fits what exists.
4. **Write** `spec.md` next to the intent from `intent/_spec-template.md`:
   - Number the requirements (R1, R2, ...) and make each one testable.
   - Show how every constraint in the intent is met, citing rule IDs (S1, A4, ...) where they apply.
   - Answer each open question from the intent or carry it forward as a concern.
   - **Areas of concern:** every rule the design cannot meet, every conflict between rules or with the
     intent, and every risk nobody has measured. Give each an owner. Don't hide them to make the spec
     look finished.
   - No code. Estimates are labelled as estimates.
5. **Review.** Show the concerns first, then the spec. Revise until the user accepts it.
6. **Commit** on a new branch `spec/short-name` from `main`, message `Spec: short name`. Don't push;
   tell the user that merging the PR accepts the spec and starts the build stage.
