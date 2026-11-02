"""Price indicators: existing close/adjusted-close conventions are kept separately."""
from __future__ import annotations

from investment_assistant.derivation.indicators._price_math import (
    ATR_PERIOD as ATR_PERIOD,
    MIN_BARS as MIN_BARS,
    _dec as _dec,
    _relative_strength as _relative_strength,
    _rolling_mean as _rolling_mean,
    atr14 as atr14,
)
from investment_assistant.derivation.indicators.registry import agent_tool_indicators


def indicators(bars: list[dict], spy_bars: list[dict]) -> dict:
    """Point-in-time indicator snapshot; anything the history is too short for is None."""
    return agent_tool_indicators(bars, spy_bars)
