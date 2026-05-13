"""Solar surplus tracking with hysteresis.

Kept HA-free so it's unit-testable without spinning up Home Assistant.
The coordinator owns one of these and feeds it (timestamp, surplus_w)
samples on every production/consumption sensor change. The tracker only
flips its `active` state after the surplus has been continuously on the
new side of the threshold for `hysteresis_seconds`, which avoids relay
chatter when surplus oscillates around the pump's draw.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SolarSurplusTracker:
    """Stateful debouncer for "is there enough solar surplus to run the pump?"."""

    pump_power_w: float
    hysteresis_seconds: int = 120
    _above_since: datetime | None = field(default=None, init=False, repr=False)
    _below_since: datetime | None = field(default=None, init=False, repr=False)
    _active: bool = field(default=False, init=False)

    @property
    def active(self) -> bool:
        """Current debounced state — whether solar should be driving the pump."""
        return self._active

    def update(self, now: datetime, surplus_w: float | None) -> bool:
        """Feed a sample. Returns the (possibly unchanged) active state.

        `surplus_w=None` means at least one underlying sensor is
        unavailable; we treat that as "no surplus" and reset both
        edge timers so we don't promote on stale data.
        """
        if surplus_w is None:
            self._above_since = None
            self._below_since = None
            self._active = False
            return False

        if surplus_w >= self.pump_power_w:
            self._below_since = None
            if self._above_since is None:
                self._above_since = now
            elif (
                not self._active
                and (now - self._above_since).total_seconds()
                >= self.hysteresis_seconds
            ):
                self._active = True
        else:
            self._above_since = None
            if self._below_since is None:
                self._below_since = now
            elif (
                self._active
                and (now - self._below_since).total_seconds()
                >= self.hysteresis_seconds
            ):
                self._active = False

        return self._active
