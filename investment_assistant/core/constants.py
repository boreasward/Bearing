"""Budget and environment constants shared across the layers (v2.12–v2.15 plan Global Constraints).

Cut verbatim from ``workbench/service.py:40-78`` and ``workbench/accounts.py`` in Task 0;
``workbench`` modules re-export from here until Task 1 deletes them. The deadline /
budget constants of the new v2.12–v2.15 features (``PM_DEADLINE_S`` …) are defined here
from the start and consumed from Task 4."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
SH = ZoneInfo('Asia/Shanghai')
BENCHMARK = 'SPY'
HISTORY_DAYS = 420          # calendar days fetched; covers MA200 plus look-backs
NEWS_LOOKBACK_DAYS = 7
OPEN_UNIT_DAYS = 14
SEC_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / '.local/us-workbench/sec-evidence'
CONNECTION_LOST = ('not_connected', 'model_unavailable')    # a unit proving the connection / model unusable stops the node (§8.8)

# The default real account (migration 030, spec D2): its id is fixed, the seed row is the only real
# account, and every pre-030 row landed in it through the column defaults.
REAL_ACCOUNT_ID = UUID('00000000-0000-0000-0000-000000000001')

# Plan level keys (was agent/tools._PLAN_LEVEL_KEYS): the inputs a plan freezes at generation.
PLAN_LEVEL_KEYS = ('entry_low', 'entry_high', 'stop', 'target1', 'ref_price', 'atr', 'sizing', 'shares',
                   'plan_version', 'valid_from', 'valid_until', 'based_on_id', 'plan_id', 'status',
                   'invalid_reasons', 'request_id', 'request_hash')
MIN_BARS_ATR = 15           # bars ATR14 needs (plans.MIN_BARS = ATR_PERIOD + 1)
MIN_BARS_RULES = 200        # bars the v1 price rules need (rules.MIN_BARS)

# Budget constants, seconds (v2.12–v2.15 plan Global Constraints).
AGENT_DEADLINE_S = 1500     # no new agent unit starts after this many seconds since the agent node started (§8.1, v2.8 D24)
PM_DEADLINE_S = 300         # v2.14 portfolio-manager unit deadline
ACCOUNTS_AGENT_DEADLINE_S = 600     # v2.11 accounts agent deadline (moved here with the base)
PROPOSE_DEADLINE_S = 600    # v2.15 propose node deadline
RESEARCH_DEADLINE_S = 900   # v2.13 researcher deadline
DATA_REQUEST_MIN_S = 120    # v2.13 minimum spacing between agent data requests
BACKUP_FILL_SESSIONS = 10   # v2.12 how many sessions a backup source may fill retroactively
RESEARCH_DAILY_MAX = 5      # v2.13 research requests accepted per day
