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

# 计划没有算出股数时面向用户的固定文案（v3.1 spec §8.3）
NO_EQUITY_TEXT = "未提供账户资金，未算股数"


def price_text(value) -> str:
    """面向用户的价格文本：去掉存储精度带来的尾零（库里是 6 位小数），不四舍五入；
    有小数时至少两位（40.5 → 40.50），整数不带小数（100.000000 → 100）。None → 「—」。"""
    if value is None:
        return "—"
    from decimal import Decimal

    number = Decimal(str(value)).normalize()
    exponent = number.as_tuple().exponent
    if exponent >= 0:
        return f"{number.quantize(Decimal(1)):f}"
    if exponent == -1:
        return f"{number.quantize(Decimal('0.01')):f}"
    return f"{number:f}"

