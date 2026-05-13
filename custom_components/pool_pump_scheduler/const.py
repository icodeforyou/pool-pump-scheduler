"""Constants for the Pool Pump Scheduler integration."""
from __future__ import annotations

from pathlib import Path
import json
from typing import Final

DOMAIN = "pool_pump_scheduler"

# Read version from manifest so the card can detect mismatches.
_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
try:
    with open(_MANIFEST_PATH, encoding="utf-8") as _f:
        INTEGRATION_VERSION: Final[str] = json.load(_f).get("version", "0.0.0")
except (OSError, json.JSONDecodeError):
    INTEGRATION_VERSION = "0.0.0"

# Base URL for frontend resources served by the integration.
URL_BASE: Final[str] = "/pool_pump_scheduler"

# List of JS modules to register as Lovelace resources.
JSMODULES: Final[list[dict[str, str]]] = [
    {
        "name": "Pool Pump Scheduler Card",
        "filename": "pool-pump-scheduler-card.js",
        "version": INTEGRATION_VERSION,
    },
]

# Config keys
CONF_PRICE_SENSOR = "price_sensor"
CONF_PUMP_SWITCH = "pump_switch"
CONF_RUNTIME_HOURS = "runtime_hours"
CONF_MIN_BLOCK_MINUTES = "min_block_minutes"
CONF_RECALC_TIME = "recalc_time"
CONF_CONTROL_SWITCH = "control_switch"
CONF_MAX_PRICE = "max_price"
CONF_USE_MAX_PRICE = "use_max_price"

# Defaults
DEFAULT_RUNTIME_HOURS = 12.0
DEFAULT_MIN_BLOCK_MINUTES = 60
DEFAULT_RECALC_TIME = "14:00:00"
DEFAULT_CONTROL_SWITCH = True
DEFAULT_USE_MAX_PRICE = False
DEFAULT_MAX_PRICE = 5.0

# Slot length is fixed at 15 minutes (Nord Pool quarter-hourly data).
SLOT_MINUTES = 15

# Attribute keys used by the Nord Pool sensor.
ATTR_RAW_TODAY = "raw_today"
ATTR_RAW_TOMORROW = "raw_tomorrow"
ATTR_TOMORROW_VALID = "tomorrow_valid"

# Service names
SERVICE_RECALCULATE = "recalculate"

# Signal names for dispatcher
SIGNAL_SCHEDULE_UPDATED = f"{DOMAIN}_schedule_updated"
