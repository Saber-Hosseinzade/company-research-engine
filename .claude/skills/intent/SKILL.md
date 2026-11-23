---
name: intent
description: Capture a new idea, request, bug or incident as intent/<date>-<name>/intent.md before any design or code. Use when the user describes something they want built or changed, or asks for a new intent.
---

# Capture an intent

Turn the user's description into a short, committed `intent.md`. No design, no code, no solutions yet.

1. **Listen.** Let the user describe the problem in their own words.
2. **Ask like an analyst** until the idea is concrete, a few questions at a time:
   - What can't be done today, and who is affected?
   - What does better look like, and how would we measure it?
   - Constraints: data, security, cost, AWS region, deadlines, what must not change?
   - What is explicitly out of scope?
   Stop asking once each template section can be filled; list what is still unknown as open questions
   instead of guessing.
3. **Write** `intent/YYYY-MM-DD-short-name/intent.md` from `intent/_template.md`, using today's date,
   `Status: draft`, and the user as author. Keep it under one page. Use the user's words; don't invent
   numbers.
4. **Review.** Show the file and ask the user to correct anything misunderstood. Revise until they agree.
5. **Commit** on a new branch `intent/short-name` from `main`, message `Intent: short name`. Don't push;
   tell the user that opening a PR and merging it accepts the intent into design, and closing it rejects it.
