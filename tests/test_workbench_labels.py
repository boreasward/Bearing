import investment_assistant.core.texts as labels
from investment_assistant.analysis import rules


def test_rule_text_covers_all_rules():
    assert set(labels.RULE_TEXT) == set(rules.PRICE_RULES) | {'new_filing'}
    assert len(labels.RULE_TEXT) == 10
    assert labels.RULE_TEXT['plan_stop'] == '计划止损' and labels.RULE_TEXT['plan_target'] == '计划目标'
    assert labels.RULE_TEXT['trend_break'] == '跌破 MA50'


def test_rule_short_same_keys_and_short():
    assert set(labels.RULE_SHORT) == set(labels.RULE_TEXT)
    assert all(0 < len(text) <= 4 for text in labels.RULE_SHORT.values())
    assert labels.RULE_SHORT['trend_reclaim'] == '回踩' and labels.RULE_SHORT['volume_breakout'] == '突破'


def test_action_text():
    assert labels.ACTION_TEXT == {'buy': '买入', 'sell': '卖出', 'watch': '观察'}
