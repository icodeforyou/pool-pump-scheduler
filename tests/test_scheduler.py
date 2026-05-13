"""Standalone tests for the scheduler DP.

Runs without Home Assistant — imports only `scheduler.py`. Invoke
with `python tests/test_scheduler.py` from the repo root.
"""
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

from scheduler import PriceSlot, compute_schedule  # noqa: E402


def _slots(values: list[float]) -> list[PriceSlot]:
    """Build contiguous 15-minute slots starting at midnight UTC."""
    base = datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc)
    return [
        PriceSlot(
            start=base + timedelta(minutes=15 * i),
            end=base + timedelta(minutes=15 * (i + 1)),
            value=v,
        )
        for i, v in enumerate(values)
    ]


def test_picks_cheap_window() -> None:
    """With one clearly cheap 2h window, the DP picks exactly that."""
    values = [1.0] * 96
    for i in range(32, 40):  # slots 32..39 cheap = 08:00 - 10:00 UTC
        values[i] = 0.1
    prices = _slots(values)
    result = compute_schedule(
        prices, runtime_hours=2.0, min_block_minutes=60, slot_minutes=15
    )
    assert result is not None
    assert result.total_slots == 8, result.total_slots
    assert result.block_count == 1, result.block_count
    assert result.blocks[0][0] == prices[32].start
    assert result.blocks[0][1] == prices[39].end


def test_min_block_enforced() -> None:
    """Every selected block must be at least min_block_minutes long."""
    values = [1.0] * 96
    values[50] = 0.01  # single very cheap slot, no 60-min cheap run
    prices = _slots(values)
    result = compute_schedule(
        prices, runtime_hours=1.0, min_block_minutes=60, slot_minutes=15
    )
    assert result is not None
    for start, end in result.blocks:
        duration_min = (end - start).total_seconds() / 60.0
        assert duration_min >= 60, duration_min


def test_max_price_filters_expensive_slots() -> None:
    """Slots above the ceiling must not appear in the schedule."""
    values = [5.0] * 96
    for i in range(32, 40):
        values[i] = 0.1
    prices = _slots(values)
    result = compute_schedule(
        prices,
        runtime_hours=2.0,
        min_block_minutes=60,
        slot_minutes=15,
        max_price=1.0,
    )
    assert result is not None
    cheap_starts = {prices[i].start for i in range(32, 40)}
    assert set(result.selected_starts) == cheap_starts


def test_returns_none_when_unsatisfiable() -> None:
    """If the ceiling+min_block leaves too few usable slots, return None."""
    values = [5.0] * 96
    for i in range(0, 4):  # only one 1h window under ceiling
        values[i] = 0.1
    prices = _slots(values)
    result = compute_schedule(
        prices,
        runtime_hours=2.0,  # needs 8 slots, only 4 usable
        min_block_minutes=60,
        slot_minutes=15,
        max_price=1.0,
    )
    assert result is None


def test_runtime_exceeds_available_data() -> None:
    """When the target exceeds available data, fall back to run-continuous."""
    prices = _slots([1.0] * 4)  # only 1 hour of data
    result = compute_schedule(
        prices, runtime_hours=2.0, min_block_minutes=60, slot_minutes=15
    )
    assert result is not None
    assert result.total_slots == 4
    assert result.block_count == 1


def test_two_separate_blocks_when_cheaper_than_one_long_block() -> None:
    """DP picks two short cheap blocks over one long expensive one."""
    values = [1.0] * 96
    # Two cheap 1h windows separated by expensive slots
    for i in range(8, 12):
        values[i] = 0.1
    for i in range(40, 44):
        values[i] = 0.1
    prices = _slots(values)
    result = compute_schedule(
        prices, runtime_hours=2.0, min_block_minutes=60, slot_minutes=15
    )
    assert result is not None
    assert result.total_slots == 8
    assert result.block_count == 2
    # Cost should equal the eight cheap slots only.
    assert abs(result.total_cost - 8 * 0.1) < 1e-9


def test_is_active_inside_block() -> None:
    """ScheduleResult.is_active correctly recognises in-block times."""
    values = [1.0] * 96
    for i in range(32, 40):
        values[i] = 0.1
    prices = _slots(values)
    result = compute_schedule(
        prices, runtime_hours=2.0, min_block_minutes=60, slot_minutes=15
    )
    assert result is not None
    block_start, block_end = result.blocks[0]
    midpoint = block_start + (block_end - block_start) / 2
    assert result.is_active(midpoint)
    assert not result.is_active(block_start - timedelta(minutes=1))
    assert not result.is_active(block_end)  # exclusive end


def main() -> None:
    tests = [
        test_picks_cheap_window,
        test_min_block_enforced,
        test_max_price_filters_expensive_slots,
        test_returns_none_when_unsatisfiable,
        test_runtime_exceeds_available_data,
        test_two_separate_blocks_when_cheaper_than_one_long_block,
        test_is_active_inside_block,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
