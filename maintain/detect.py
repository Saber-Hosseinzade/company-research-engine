"""Control-band detection: which tier does the newest reading land in?

Reads a JSON list of readings on stdin, oldest first, and prints the worst tier the newest one trips,
as JSON. Deterministic by design: no model is involved in deciding whether something is wrong.
The baseline is every reading before the newest; sigma is its standard deviation. Rules follow the
Western Electric style, high side only (for error and failure rates):
3 sigma, 2 sigma, 4 of the last 5 above 1 sigma, 8 in a row above the mean (drift counts as 2 sigma).
"""

import json
import statistics
import sys
from typing import Any

MIN_READINGS = 14  # baseline readings needed before any band is trusted
MIN_SIGMA = 0.01  # in the metric's own units, so a flat baseline doesn't turn every blip into a breach


def above(readings: list[float], line: float, need: int, out_of: int) -> bool:
    """True if at least `need` of the last `out_of` readings sit above `line`."""
    return sum(r > line for r in readings[-out_of:]) >= need


def tier(
    readings: list[float], min_readings: int = MIN_READINGS, min_sigma: float = MIN_SIGMA
) -> dict[str, Any]:
    baseline = readings[:-1]
    if len(baseline) < min_readings:
        return {"tier": "none", "rule": f"not enough readings ({len(baseline)} of {min_readings})"}
    mean = statistics.mean(baseline)
    sigma = max(statistics.stdev(baseline), min_sigma)
    rules = [  # worst first
        ("3sigma", "reading above 3 sigma", above(readings, mean + 3 * sigma, 1, 1)),
        ("2sigma", "reading above 2 sigma", above(readings, mean + 2 * sigma, 1, 1)),
        ("2sigma", "drift: 4 of 5 readings above 1 sigma", above(readings, mean + sigma, 4, 5)),
        ("2sigma", "drift: 8 readings in a row above the mean", above(readings, mean, 8, 8)),
        ("1sigma", "reading above 1 sigma", above(readings, mean + sigma, 1, 1)),
    ]
    result: dict[str, Any] = {"reading": readings[-1], "mean": round(mean, 4), "sigma": round(sigma, 4)}
    for name, rule, tripped in rules:
        if tripped:
            return {"tier": name, "rule": rule, **result}
    return {"tier": "none", "rule": "within band", **result}


if __name__ == "__main__":
    print(json.dumps(tier([float(r) for r in json.load(sys.stdin)])))
