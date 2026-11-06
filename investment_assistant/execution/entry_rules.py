"""Entry types and the entry rules version `entry-1`: the three entry types a plan can carry and the default one."""
from __future__ import annotations

ENTRY_RULES_VERSION = 'entry-1'

ENTRY_TYPES = ('zone_limit', 'breakout_stop', 'next_open')
DEFAULT_ENTRY_TYPE = 'zone_limit'
