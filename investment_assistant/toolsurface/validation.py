"""Ticker normalization, brief/chat scope and the host's agent-level guard rails (pure)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from investment_assistant.execution.plan_levels import levels_ordered

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def normalize_ticker(value: Any) -> str:
    """小写、首尾空格都归一成大写；任何不合法形状（含非字符串）都是 ValueError('invalid_ticker')。"""
    text = value.strip().upper() if isinstance(value, str) else ""
    if not TICKER_RE.match(text):
        raise ValueError("invalid_ticker")
    return text


def in_scope(mode: str, ticker: str, top: list[str]) -> bool:
    """brief：只认当期名单前 N 只；chat：恒真（用户可以点名任何标的）。"""
    return True if mode == "chat" else ticker in top


def check_agent_levels(levels: dict[str, Decimal], *, last_close: Decimal, atr: Decimal) -> list[str]:
    """Agent 自报价位的四条宿主护栏（spec §6）：先顺序，再入场带位置、止损距离与 1R 回报。"""
    if not levels_ordered(levels):
        return ["unordered"]
    problems: list[str] = []
    if levels["entry_high"] > last_close + atr:
        problems.append("entry_too_high")
    if levels["entry_low"] < last_close - 2 * atr:
        problems.append("entry_too_low")
    stop_distance = levels["entry_low"] - levels["stop"]
    if stop_distance < atr / 2:
        problems.append("stop_too_tight")
    if stop_distance > 3 * atr:
        problems.append("stop_too_wide")
    if levels["target1"] - levels["entry_high"] < levels["entry_high"] - levels["stop"]:
        problems.append("reward_below_1r")
    return problems


class ToolError(ValueError):
    """A coded rejection whose envelope carries `detail` (e.g. the plan that blocks a save)."""

    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.detail = detail


def text_arg(value: Any, name: str) -> str:
    """非空字符串；其余输入 ValueError('invalid_argument: …')。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid_argument: {name} 必须是非空字符串")
    return value


def number_arg(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    """JSON 数值转 Decimal；bool 不是数，零只在允许时有效。"""
    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if number is None:
        raise ValueError(f"invalid_argument: {name} 必须是数字")
    try:
        converted = Decimal(str(number))
    except InvalidOperation:
        raise ValueError(f"invalid_argument: {name} 必须是数字") from None
    if converted < 0 or (converted == 0 and not allow_zero):
        raise ValueError(f"invalid_argument: {name} 必须大于零")
    return converted


def signed_number_arg(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    """同 number_arg 但允许负数（调整类出入金）。"""
    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if number is None:
        raise ValueError(f"invalid_argument: {name} 必须是数字")
    try:
        converted = Decimal(str(number))
    except InvalidOperation:
        raise ValueError(f"invalid_argument: {name} 必须是数字") from None
    if converted == 0 and not allow_zero:
        raise ValueError(f"invalid_argument: {name} 不能为零")
    return converted


def confirmed_flag(value: Any) -> bool:
    """非布尔输入按 invalid_argument 错误信封拒绝（工具面错误码约定用 ValueError）。"""
    if not isinstance(value, bool):
        raise ValueError("invalid_argument: confirmed 必须是布尔值")
    return value


def note_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_argument: note 必须是字符串")
    return value


def datetime_arg(value: Any, name: str) -> datetime:
    text = value if isinstance(value, str) else ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"invalid_argument: {name} 必须是 ISO 时间") from None
    if parsed.tzinfo is None:
        raise ValueError(f"invalid_argument: {name} 需带时区")
    return parsed


def date_arg(value: Any, name: str) -> date:
    text = value if isinstance(value, str) else ""
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"invalid_argument: {name} 必须是 YYYY-MM-DD") from None
