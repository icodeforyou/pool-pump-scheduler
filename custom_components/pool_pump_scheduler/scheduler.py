"""Scheduling algorithm for the pool pump.

Selects the cheapest set of contiguous time blocks that:
- Sum to at least the required runtime (in slots)
- Each block has at least `min_block_slots` consecutive slots
- Optionally, every selected slot is below a max price ceiling

Approach
--------
We use dynamic programming over the price array. For each position i, we
track the minimum cost to have selected `j` slots so far, where the state
also encodes whether we're currently "inside" a block and how many slots
have been added to the current block (to enforce minimum block length when
closing it).

To keep the state space small, we precompute cumulative sums and enumerate
candidate blocks (start, length) with length >= min_block_slots. Then we
pick a subset of non-overlapping blocks whose total length >= target,
minimizing total cost. This is a weighted interval-selection variant solved
with DP in O(n^2) which is fine for n=96 or n=192 (today+tomorrow).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import math

_LOGGER = logging.getLogger(__name__)


@dataclass
class PriceSlot:
    """A single price slot."""

    start: datetime
    end: datetime
    value: float


@dataclass
class ScheduleResult:
    """The output of a scheduling run."""

    selected_starts: list[datetime]
    total_cost: float
    block_count: int
    total_slots: int
    blocks: list[tuple[datetime, datetime]]  # (start, end) per block

    def is_active(self, when: datetime) -> bool:
        """Return True if the given time falls in any selected block."""
        for start, end in self.blocks:
            if start <= when < end:
                return True
        return False


def compute_schedule(
    prices: list[PriceSlot],
    runtime_hours: float,
    min_block_minutes: int,
    slot_minutes: int = 15,
    max_price: float | None = None,
) -> ScheduleResult | None:
    """Compute the cheapest schedule meeting the constraints.

    Args:
        prices: List of price slots (must be contiguous, in time order).
        runtime_hours: Minimum total runtime required.
        min_block_minutes: Minimum length of any single ON block.
        slot_minutes: Length of one price slot (typically 15).
        max_price: If set, slots above this price are excluded entirely.

    Returns:
        ScheduleResult or None if no valid schedule exists.
    """
    if not prices:
        return None

    n = len(prices)
    slots_per_hour = 60 // slot_minutes
    target_slots = math.ceil(runtime_hours * slots_per_hour)
    min_block_slots = max(1, math.ceil(min_block_minutes / slot_minutes))

    if target_slots > n:
        _LOGGER.warning(
            "Target runtime %s h (%d slots) exceeds available data (%d slots). "
            "Will run continuously.",
            runtime_hours,
            target_slots,
            n,
        )
        return ScheduleResult(
            selected_starts=[p.start for p in prices],
            total_cost=sum(p.value for p in prices),
            block_count=1,
            total_slots=n,
            blocks=[(prices[0].start, prices[-1].end)],
        )

    # Mark slots that exceed the price ceiling as unusable.
    if max_price is not None:
        usable = [p.value <= max_price for p in prices]
    else:
        usable = [True] * n

    # Enumerate all candidate blocks: every contiguous run of usable slots
    # of length >= min_block_slots gives candidate sub-blocks.
    # For each start i, for each length L >= min_block_slots up to remaining
    # usable run, create a candidate.
    candidates: list[tuple[int, int, float]] = []  # (start_idx, length, cost)

    # Precompute prefix sums (only over usable slots; non-usable break runs).
    i = 0
    while i < n:
        if not usable[i]:
            i += 1
            continue
        # Find end of this usable run.
        j = i
        while j < n and usable[j]:
            j += 1
        # Run is [i, j). Enumerate all sub-blocks of length >= min_block_slots.
        run_prefix = [0.0]
        for k in range(i, j):
            run_prefix.append(run_prefix[-1] + prices[k].value)
        run_len = j - i
        for length in range(min_block_slots, run_len + 1):
            for start_off in range(0, run_len - length + 1):
                cost = run_prefix[start_off + length] - run_prefix[start_off]
                candidates.append((i + start_off, length, cost))
        i = j

    if not candidates:
        _LOGGER.warning(
            "No candidate blocks of length >= %d slots found. "
            "Check min_block_minutes vs available cheap slots.",
            min_block_slots,
        )
        return None

    # DP: dp[i][k] = (min_cost, parent) for "considering slot i, k slots
    # selected so far, no block currently overlapping i".
    # State is keyed by (position, slots_selected). For each state, we
    # either advance position by 1 (skip), or place a candidate block
    # starting at position i (if available), then jump to i+length.
    #
    # We cap k at target_slots (any more is wasted).
    INF = float("inf")
    # dp[i][k] -> (cost, back_pointer_block_index_or_None, prev_k)
    dp: list[list[tuple[float, int | None, int]]] = [
        [(INF, None, 0) for _ in range(target_slots + 1)] for _ in range(n + 1)
    ]
    dp[0][0] = (0.0, None, 0)

    # Index candidates by start position for fast lookup.
    cands_by_start: dict[int, list[tuple[int, int, float, int]]] = {}
    for idx, (start, length, cost) in enumerate(candidates):
        cands_by_start.setdefault(start, []).append((start, length, cost, idx))

    for i in range(n + 1):
        for k in range(target_slots + 1):
            cur_cost, _, _ = dp[i][k]
            if cur_cost == INF:
                continue
            # Option 1: skip slot i (advance without placing a block).
            if i < n:
                if cur_cost < dp[i + 1][k][0]:
                    dp[i + 1][k] = (cur_cost, None, k)
            # Option 2: place a candidate block starting at i.
            if i < n and i in cands_by_start:
                for (start, length, cost, idx) in cands_by_start[i]:
                    new_k = min(k + length, target_slots)
                    new_i = i + length
                    if new_i > n:
                        continue
                    new_cost = cur_cost + cost
                    if new_cost < dp[new_i][new_k][0]:
                        dp[new_i][new_k] = (new_cost, idx, k)

    # Find best terminal state with k >= target_slots.
    best_cost = INF
    best_i = -1
    best_k = -1
    for i in range(n + 1):
        cost, _, _ = dp[i][target_slots]
        if cost < best_cost:
            best_cost = cost
            best_i = i
            best_k = target_slots

    if best_i < 0 or best_cost == INF:
        _LOGGER.warning(
            "Could not satisfy runtime target of %d slots with min block %d slots.",
            target_slots,
            min_block_slots,
        )
        return None

    # Reconstruct: walk backwards collecting placed block indices.
    placed: list[int] = []
    i, k = best_i, best_k
    while i > 0 or k > 0:
        cost, idx, prev_k = dp[i][k]
        if idx is not None:
            placed.append(idx)
            start, length, _ = candidates[idx]
            i = start
            k = prev_k
        else:
            i -= 1
            k = prev_k
    placed.reverse()

    blocks: list[tuple[datetime, datetime]] = []
    selected_starts: list[datetime] = []
    for idx in placed:
        start, length, _ = candidates[idx]
        block_start = prices[start].start
        block_end = prices[start + length - 1].end
        blocks.append((block_start, block_end))
        for s in range(start, start + length):
            selected_starts.append(prices[s].start)

    return ScheduleResult(
        selected_starts=selected_starts,
        total_cost=best_cost,
        block_count=len(blocks),
        total_slots=len(selected_starts),
        blocks=blocks,
    )
