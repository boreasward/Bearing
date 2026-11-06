import investment_assistant.core.texts as labels
from investment_assistant.analysis import rules


def test_rule_text_covers_all_rules():
    assert set(labels.RULE_TEXT) == set(rules.PRICE_RULES) | {'new_filing'}
    assert len(labels.RULE_TEXT) == 10
    assert labels.RULE_TEXT['plan_stop'] == '计划止损' and labels.RULE_TEXT['plan_target'] == '计划目标'
    assert labels.RULE_TEXT['trend_break'] == '跌破 MA50'
