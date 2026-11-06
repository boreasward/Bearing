"""Environment constants shared across the layers."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
BENCHMARK = 'SPY'
HISTORY_DAYS = 420          # calendar days fetched; covers MA200 plus look-backs
SEC_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / '.local/us-workbench/sec-evidence'

MIN_BARS_ATR = 15           # bars ATR14 needs (plans.MIN_BARS = ATR_PERIOD + 1)

BACKUP_FILL_SESSIONS = 10   # how many sessions a backup source may fill retroactively
