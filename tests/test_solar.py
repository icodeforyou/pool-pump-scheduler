"""Standalone tests for the solar surplus hysteresis tracker."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "pool_pump_scheduler"
    ),
)

from solar import SolarSurplusTracker  # noqa: E402


T0 = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_inactive_initially() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    assert t.active is False


def test_promotes_after_hysteresis_above_threshold() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    # Above threshold but only 60s in — not yet active
    t.update(_at(0), 1000.0)
    assert t.update(_at(60), 1000.0) is False
    # 120s in — flips active
    assert t.update(_at(120), 1000.0) is True


def test_does_not_promote_if_surplus_drops_briefly() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    t.update(_at(0), 1000.0)
    t.update(_at(60), 1000.0)
    # Brief dip below resets the above-timer
    t.update(_at(70), 500.0)
    # Back above, but the 120s clock started over
    t.update(_at(80), 1000.0)
    assert t.update(_at(180), 1000.0) is False
    assert t.update(_at(200), 1000.0) is True


def test_demotes_after_hysteresis_below_threshold() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    t.update(_at(0), 1000.0)
    t.update(_at(120), 1000.0)
    assert t.active is True
    # First below-sample at 130s. 110s later (240s) the demote window hasn't
    # fully elapsed — still active.
    t.update(_at(130), 500.0)
    assert t.update(_at(240), 500.0) is True
    # 120s after the first below-sample (= 250s) — demoted.
    assert t.update(_at(250), 500.0) is False


def test_brief_spike_above_does_not_re_promote_immediately() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    # Promote
    t.update(_at(0), 1000.0)
    t.update(_at(120), 1000.0)
    assert t.active is True
    # Drop sustained, demote
    t.update(_at(130), 200.0)
    t.update(_at(250), 200.0)
    assert t.active is False
    # Brief spike above
    t.update(_at(260), 1000.0)
    # Drops again immediately
    assert t.update(_at(265), 200.0) is False


def test_unavailable_resets_state() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    t.update(_at(0), 1000.0)
    t.update(_at(120), 1000.0)
    assert t.active is True
    # Sensor goes unavailable — immediate reset, no hysteresis
    assert t.update(_at(125), None) is False
    assert t.active is False


def test_exactly_at_threshold_counts_as_above() -> None:
    t = SolarSurplusTracker(pump_power_w=750, hysteresis_seconds=120)
    t.update(_at(0), 750.0)
    assert t.update(_at(120), 750.0) is True


def main() -> None:
    tests = [
        test_inactive_initially,
        test_promotes_after_hysteresis_above_threshold,
        test_does_not_promote_if_surplus_drops_briefly,
        test_demotes_after_hysteresis_below_threshold,
        test_brief_spike_above_does_not_re_promote_immediately,
        test_unavailable_resets_state,
        test_exactly_at_threshold_counts_as_above,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
