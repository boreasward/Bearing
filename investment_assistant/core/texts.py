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


def price_value(value):
    """给用户看的价格（Decimal）：去掉存储精度带来的尾零（库里是 6 位小数），不四舍五入；
    有小数时至少两位（40.5 → 40.50），整数不带小数（100.000000 → 100）。None 原样返回。"""
    if value is None:
        return None
    from decimal import Decimal

    number = Decimal(str(value)).normalize()
    exponent = number.as_tuple().exponent
    if exponent >= 0:
        return number.quantize(Decimal(1))
    if exponent == -1:
        return number.quantize(Decimal("0.01"))
    return number


def price_text(value) -> str:
    """price_value 的文本形式；None → 「—」。"""
    return "—" if value is None else f"{price_value(value):f}"


def money_value(value):
    """给用户看的金额（Decimal）：取到分、四舍五入（-2736.000000000000 → -2736.00）。None 原样返回。"""
    if value is None:
        return None
    from decimal import ROUND_HALF_UP, Decimal

    return Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def quantity_value(value):
    """给用户看的股数（Decimal）：去掉存储尾零（120.000000 → 120，0.500000 → 0.5）。None 原样返回。"""
    if value is None:
        return None
    from decimal import Decimal

    number = Decimal(str(value)).normalize()
    return number.quantize(Decimal(1)) if number == number.to_integral_value() else number
