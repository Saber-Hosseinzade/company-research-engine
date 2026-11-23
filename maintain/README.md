# Maintain: closing the loop on a metric

A scheduled workflow (`.github/workflows/maintain.yml`) watches one metric and restarts the lifecycle
when it breaks its control band, without anyone starting it.

## How it works

1. **Collect:** `readings.py` turns the tests workflow's run history on `main` into one failure rate per
   day.
2. **Detect:** `detect.py` compares the newest reading with the earlier ones (mean and sigma) and prints
   a tier. It is plain Python with unit tests (`tests/test_maintain.py`); no model decides whether
   something is wrong.
3. **Respond:** `bands.toml` maps the tier to an action and to the only tools Claude may use:

   | Tier | Trips on | Action |
   | --- | --- | --- |
   | 1 sigma | one reading above mean + 1 sigma | log only (noise trips this often) |
   | 2 sigma | above 2 sigma, or drift: 4 of 5 above 1 sigma, 8 in a row above the mean | Claude diagnoses, read-only |
   | 3 sigma | above 3 sigma | Claude also writes `intent/<date>-ci-failure-rate/intent.md`; the workflow opens it as a PR |

4. **Triage:** merge the intent PR to accept it into design, or close it to dismiss. Repeated dismissals
   mean the bands are too tight: tune `bands.toml` or the minimums in `detect.py`.
5. **Remember:** when the fix ships, add an eval for it in `evals/` so the same failure can't return.

Bands need history: until there are 14 days with CI runs, every reading reports `none`.

## Adding a metric

Copy the pattern for a production metric (5xx rate, latency, cost per run, eval pass rate): a script
that prints readings, the same `detect.py`, a tier table in `bands.toml`, and a scheduled job. Actions
that change a live system (rollback) must be pre-approved runbooks behind the approval gate.

## Measure

- Time from a band breach to an intent PR.
- Share of intent PRs merged rather than closed; repeat incidents of the same kind.
