"""Offline tests for the control-band detector and the CI readings."""

import pytest
from detect import tier
from readings import daily_failure_rates

STEADY = [0.10, 0.12, 0.08, 0.11, 0.09, 0.10, 0.12, 0.08, 0.10, 0.11, 0.09, 0.10, 0.12, 0.08]


def test_normal_reading_is_within_band() -> None:
    assert tier([*STEADY, 0.10])["tier"] == "none"


def test_spike_trips_3_sigma() -> None:
    result = tier([*STEADY, 0.90])
    assert (result["tier"], result["rule"]) == ("3sigma", "reading above 3 sigma")


def test_moderate_reading_trips_1_sigma_only() -> None:
    assert tier([*STEADY, 0.12])["tier"] == "1sigma"  # 1 sigma = 0.1147, 2 sigma = 0.1294


def test_slow_drift_trips_2_sigma_without_crossing_it() -> None:
    drift = [0.11] * 8  # above the mean, below 1 sigma: only the 8-in-a-row rule catches it
    result = tier([*STEADY, *drift])
    assert (result["tier"], result["rule"]) == ("2sigma", "drift: 8 readings in a row above the mean")


def test_too_few_readings_never_trips() -> None:
    assert tier([0.0, 0.0, 1.0])["tier"] == "none"


def test_flat_baseline_uses_minimum_sigma() -> None:
    assert tier([0.0] * 14 + [0.005])["tier"] == "none"
    assert tier([0.0] * 14 + [0.05])["tier"] == "3sigma"


def test_daily_rates_skip_cancelled_and_group_by_day() -> None:
    runs = [
        {"conclusion": "success", "createdAt": "2026-11-01T09:00:00Z"},
        {"conclusion": "failure", "createdAt": "2026-11-01T10:00:00Z"},
        {"conclusion": "cancelled", "createdAt": "2026-11-01T11:00:00Z"},
        {"conclusion": "success", "createdAt": "2026-11-02T09:00:00Z"},
    ]
    assert daily_failure_rates(runs) == pytest.approx([0.5, 0.0])
