"""Ticker normalization, brief/chat scope and the host's agent-level guard rails (pure)."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from investment_assistant.execution.plan_levels import levels_ordered

TICKER_RE = re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$')


def normalize_ticker(value: Any) -> str:
    """小写、首尾空格都归一成大写；任何不合法形状（含非字符串）都是 ValueError('invalid_ticker')。"""
    text = value.strip().upper() if isinstance(value, str) else ''
    if not TICKER_RE.match(text):
        raise ValueError('invalid_ticker')
    return text


def in_scope(mode: str, ticker: str, top: list[str]) -> bool:
    """brief：只认当期名单前 N 只；chat：恒真（用户可以点名任何标的）。"""
    return True if mode == 'chat' else ticker in top


def check_agent_levels(levels: dict[str, Decimal], *, last_close: Decimal, atr: Decimal) -> list[str]:
    """Agent 自报价位的四条宿主护栏（spec §6）：先顺序，再入场带位置、止损距离与 1R 回报。"""
    if not levels_ordered(levels):
        return ['unordered']
    problems: list[str] = []
    if levels['entry_high'] > last_close + atr:
        problems.append('entry_too_high')
    if levels['entry_low'] < last_close - 2 * atr:
        problems.append('entry_too_low')
    stop_distance = levels['entry_low'] - levels['stop']
    if stop_distance < atr / 2:
        problems.append('stop_too_tight')
    if stop_distance > 3 * atr:
        problems.append('stop_too_wide')
    if levels['target1'] - levels['entry_high'] < levels['entry_high'] - levels['stop']:
        problems.append('reward_below_1r')
    return problems
