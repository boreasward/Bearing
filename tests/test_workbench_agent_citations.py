"""Proposal and counter citation checks (P3 design §6.2–6.3): pure functions, no model."""
import json
from decimal import Decimal

import pytest

from investment_assistant.execution.citations import LEVEL_BAND, Rejected, check_proposal, filter_counter

LAST_CLOSE = Decimal('100')
REFS = {'call:1', 'call:2', 'call:3'}
PRICE_REFS = {'call:1'}   # only get_indicators counts as a price tool here


def _buy(**overrides):
    payload = {
        'action': 'buy', 'summary': '回踩修复后放量上行',
        'levels': {
            'entry_low': {'value': 98.5, 'refs': ['call:1']},
            'entry_high': {'value': 101.5, 'refs': ['call:1']},
            'stop': {'value': 96.0, 'refs': ['call:1']},
            'target1': {'value': 108.0, 'refs': ['call:1']},
        },
        'thesis': [{'text': '放量突破 20 日均线', 'refs': ['call:1', 'call:2']}],
    }
    for key in ('entry_low', 'entry_high', 'stop', 'target1'):
        if key in overrides:                      # single-level overrides land inside levels
            payload['levels'][key] = overrides.pop(key)
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def check(text, *, refs=REFS, price_refs=PRICE_REFS, last_close=LAST_CLOSE, **kwargs):
    return check_proposal(text, refs=refs, price_refs=price_refs, last_close=last_close, **kwargs)


def test_fenced_json_accepted():
    out = check(f"```json\n{_buy()}\n```")
    assert out['action'] == 'buy'
    assert out['summary'] == '回踩修复后放量上行'
    assert out['levels']['entry_low'] == {'value': Decimal('98.50'), 'refs': ['call:1']}
    assert out['thesis'] == [{'text': '放量突破 20 日均线', 'refs': ['call:1', 'call:2']}]


def test_numeric_strings_accepted():
    text = json.dumps({'action': 'buy', 'summary': 's',
                       'levels': {'entry_low': {'value': '123.40', 'refs': ['call:1']},
                                  'entry_high': {'value': '126.00', 'refs': ['call:1']},
                                  'stop': {'value': '110.00', 'refs': ['call:1']},
                                  'target1': {'value': '140.00', 'refs': ['call:1']}},
                       'thesis': [{'text': 't', 'refs': ['call:1']}]})
    out = check(text)
    assert out['levels']['entry_low']['value'] == Decimal('123.40')


def test_prose_around_json_is_malformed():
    with pytest.raises(Rejected) as ei:
        check(f"好的，以下是我的建议：{_buy()}")
    assert ei.value.reason == 'malformed'
    with pytest.raises(Rejected) as ei:
        check(f"{_buy()}\n以上仅供参考。")
    assert ei.value.reason == 'malformed'


def test_missing_thesis_malformed():
    with pytest.raises(Rejected) as ei:
        check(_buy(thesis=[]))
    assert ei.value.reason == 'malformed'


def test_bad_action_malformed():
    with pytest.raises(Rejected) as ei:
        check(_buy(action='hold'))
    assert ei.value.reason == 'malformed'


def test_buy_without_levels_malformed():
    text = json.dumps({'action': 'buy', 'summary': 's', 'thesis': [{'text': 't', 'refs': ['call:1']}]})
    with pytest.raises(Rejected) as ei:
        check(text)
    assert ei.value.reason == 'malformed'


def test_pass_without_levels_ok():
    text = json.dumps({'action': 'pass', 'summary': '趋势不明',
                       'thesis': [{'text': '量能不足', 'refs': ['call:2']}]})
    out = check(text)
    assert out['action'] == 'pass' and out['levels'] is None
    assert out['thesis'] == [{'text': '量能不足', 'refs': ['call:2']}]


def test_unknown_ref_rejects_whole_proposal():
    with pytest.raises(Rejected) as ei:
        check(_buy(thesis=[{'text': '强', 'refs': ['call:99']}]))
    assert ei.value.reason == 'invalid_citation'
    with pytest.raises(Rejected) as ei:
        check(_buy(thesis=[{'text': '强', 'refs': ['call3']}]))
    assert ei.value.reason == 'invalid_citation'


def test_level_without_price_ref_rejected():
    levels = {k: {'value': v['value'], 'refs': ['call:2']}           # call:2 is get_news, not a price tool
              for k, v in json.loads(_buy())['levels'].items()}
    with pytest.raises(Rejected) as ei:
        check(_buy(levels=levels))
    assert ei.value.reason == 'invalid_citation'


def test_level_order_and_band():
    assert LEVEL_BAND == Decimal('0.5')
    with pytest.raises(Rejected) as ei:
        check(_buy(stop={'value': 100, 'refs': ['call:1']}))          # stop >= entry_low
    assert ei.value.reason == 'level_check'
    with pytest.raises(Rejected) as ei:
        check(_buy(target1={'value': 160, 'refs': ['call:1']}))       # last_close × 1.6 out of band
    assert ei.value.reason == 'level_check'
    out = check(_buy(target1={'value': 150, 'refs': ['call:1']}))     # × 1.5 boundary passes
    assert out['levels']['target1']['value'] == Decimal('150.00')


def test_counter_drops_unknown_refs():
    text = json.dumps({'risks': [
        {'text': '财报临近', 'refs': ['call:1']},
        {'text': '高位放量滞涨', 'refs': ['call:9']},
        {'text': '行业整体走弱', 'refs': ['call:2']},
    ]})
    assert filter_counter(text, refs=REFS) == {
        'status': 'ok',
        'risks': [{'text': '财报临近', 'refs': ['call:1']}, {'text': '行业整体走弱', 'refs': ['call:2']}],
        'dropped': 1}


def test_counter_ignores_levels_and_action():
    text = json.dumps({'action': 'pass', 'levels': {'stop': 1}, 'risks': [{'text': '风险一', 'refs': ['call:3']}]})
    out = filter_counter(text, refs=REFS)
    assert out['status'] == 'ok' and out['risks'] == [{'text': '风险一', 'refs': ['call:3']}] and out['dropped'] == 0
    assert 'levels' not in out and 'action' not in out


def test_counter_malformed():
    assert filter_counter('我认为没有风险', refs=REFS) == {'status': 'malformed', 'risks': [], 'dropped': 0}


# --- check_interpretation (P4 design §7.2) ---

def _interpret(**overrides):
    payload = {'summary': '量能扩张，趋势延续', 'support': [{'text': '放量突破 20 日线', 'refs': ['call:1']}],
               'contra': [], 'conflicts': []}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def check_interpret(text, *, refs=REFS):
    from investment_assistant.execution.citations import check_interpretation
    return check_interpretation(text, refs=refs)


def test_interpretation_fenced_ok():
    fence = chr(96) * 3
    out = check_interpret(fence + '\n' + _interpret() + '\n' + fence)
    assert out['summary'] == '量能扩张，趋势延续'
    assert out['support'] == [{'text': '放量突破 20 日线', 'refs': ['call:1']}]
    assert out['contra'] == [] and out['conflicts'] == []


def test_interpretation_empty_lists_ok():
    out = check_interpret(_interpret(support=[], contra=[], conflicts=[]))
    assert out == {'summary': '量能扩张，趋势延续', 'support': [], 'contra': [], 'conflicts': []}


def test_interpretation_missing_summary_malformed():
    with pytest.raises(Rejected) as exc:
        check_interpret('{' + json.dumps({'support': [], 'contra': [], 'conflicts': []}) + '}')
    assert exc.value.reason == 'malformed'


def test_interpretation_item_without_refs_malformed():
    with pytest.raises(Rejected) as exc:
        check_interpret(_interpret(support=[{'text': '缺引用'}]))
    assert exc.value.reason == 'malformed'


def test_interpretation_unknown_ref_invalid():
    with pytest.raises(Rejected) as exc:
        check_interpret(_interpret(support=[{'text': '引用不存在', 'refs': ['call:42']}]))
    assert exc.value.reason == 'invalid_citation'


def test_interpretation_drops_action_and_levels():
    out = check_interpret(_interpret(action='buy', levels={'stop': 95}))
    assert 'action' not in out and 'levels' not in out


# --- v2.9 spec §21 R3: holding-phase level ordering ---

def _above_entry(**overrides):
    """A holding revision that moved the stop up through the entry zone (stop 99 >= entry_low 98.5)."""
    return _buy(stop={'value': 99.0, 'refs': ['call:1']}, **overrides)


def test_holding_levels_allow_stop_above_entry():
    out = check(_above_entry(), mode='holding')
    assert out['levels']['stop']['value'] == Decimal('99.00')
    assert out['levels']['entry_low']['value'] == Decimal('98.50')
    # the same payload is a level_check failure for an entry-phase proposal (the default)
    for kwargs in ({}, {'mode': 'entry'}):
        with pytest.raises(Rejected) as ei:
            check(_above_entry(), **kwargs)
        assert ei.value.reason == 'level_check'


def test_holding_levels_still_ordered_against_target():
    for bad in (_buy(stop={'value': 108.0, 'refs': ['call:1']}),          # stop == target1
                _buy(stop={'value': 109.0, 'refs': ['call:1']}),          # stop above target1
                _buy(entry_high={'value': 108.0, 'refs': ['call:1']}),    # entry_high == target1
                _buy(entry_low={'value': 102.0, 'refs': ['call:1']})):    # entry_low > entry_high
        with pytest.raises(Rejected) as ei:
            check(bad, mode='holding')
        assert ei.value.reason == 'level_check'
    with pytest.raises(Rejected) as ei:                                    # the ±50 % band holds in both modes
        check(_buy(stop={'value': 40.0, 'refs': ['call:1']}), mode='holding')
    assert ei.value.reason == 'level_check'


def test_unknown_mode_is_a_programming_error():
    with pytest.raises(ValueError):
        check(_buy(), mode='exiting')
