from decimal import Decimal as D

import investment_assistant.core.texts as labels
from investment_assistant.analysis import rules


def test_rule_text_covers_all_rules():
    assert set(labels.RULE_TEXT) == set(rules.PRICE_RULES) | {"new_filing"}
    assert len(labels.RULE_TEXT) == 10
    assert labels.RULE_TEXT["plan_stop"] == "计划止损" and labels.RULE_TEXT["plan_target"] == "计划目标"
    assert labels.RULE_TEXT["trend_break"] == "跌破 MA50"


def test_price_value_two_decimals():
    """spec B14：价格取到分（ROUND_HALF_UP），与金额同口径；库里精度不变，只改显示。"""
    assert labels.price_value(D("40.5")) == D("40.50")
    assert labels.price_value(D("98.000000")) == D("98.00")
    assert labels.price_value(D("123.4567")) == D("123.46")
    assert labels.price_value(None) is None
