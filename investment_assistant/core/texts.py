"""Chinese text tables shared by the rule, regime and draft modules.

Names here are unambiguous across the whole backend; the regime labels are ``REGIME_LABEL_TEXT``."""
from __future__ import annotations

RULE_TEXT: dict[str, str] = {
    'trend_reclaim': '趋势回踩修复',
    'volume_breakout': '放量突破',
    'earnings_gap_up': '财报跳空向上',
    'trend_break': '跌破 MA50',
    'earnings_gap_down': '财报跳空向下',
    'position_drawdown': '持仓回撤',
    'new_filing': '新财报',
    'abnormal_move': '异常波动',
    'plan_stop': '计划止损',
    'plan_target': '计划目标',
}

REGIME_LABEL_TEXT: dict[str, str] = {'risk_on': '进攻', 'neutral': '中性', 'risk_off': '防守',
                                     'insufficient': '样本不足'}
