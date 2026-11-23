---
name: bugfix
description: Fix a bug test-first - reproduce it as a failing test, commit the test, lock the tests, then fix the code. Use when the user reports a bug or asks for a fix.
---

# Fix a bug, test first

1. **Reproduce.** Write a test in `tests/` that shows the bug. Run it and confirm it fails *for the
   reason you expect*; show the failure. If it fails for another reason, fix the test first.
2. **Commit the test** on the change's branch (`fix/short-name`), message `Test: <bug>`.
3. **Lock the tests:** create the empty file `.claude/tests-locked`. A hook now blocks edits to `tests/`.
4. **Fix the code** until the new test and all others pass. Never edit, skip or delete a test; if you
   believe the test itself is wrong, stop and tell the user.
5. **Verify:** run `make check typecheck test` and show the output.
6. Tell the user to review and run `make tests-unlock` when done.
