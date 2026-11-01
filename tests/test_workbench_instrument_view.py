"""Instrument page pure functions (v2.6 spec §9.1–§9.2, D14): focus items, key data, background context."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import investment_assistant.views.instrument as instrument_view
import investment_assistant.views.reasoning as reasoning
from investment_assistant.analysis.agent import tools

SESSION = date(2026, 10, 22)                                   # Thursday
HEAD = '33333333-3333-4333-8333-333333333333'
NONE_TEXT = '今天没有触发，也没有进行中的计划或持仓'


def position(**extra):
    return {'quantity': Decimal('10'), 'avg_cost': Decimal('95.5'), 'stop': Decimal('92'), 'plan_id': 'p1',
            'to_stop_pct': 0.0543, **extra}


def chain(**extra):
    return {'signal_date': date(2026, 10, 21), 'state': 'entry', 'mode': 'entry', 'version_no': 1,
            'head_plan_id': HEAD, 'entry_low': Decimal('98.5'), 'entry_high': Decimal('101.5'),
            'stop': Decimal('96'), 'target1': Decimal('108'),
            'latest_review': {'outcome': 'maintain', 'decided_by': 'host',
                              'created_at': datetime(2026, 10, 26, 22, 30, tzinfo=UTC)}, **extra}


def cand(rule_id, action):
    return {'id': f'c-{rule_id}', 'rule_id': rule_id, 'action': action}


def focus(**kwargs):
    base = dict(position=None, chains=[], candidates_today=[], earnings=None, session=SESSION)
    base.update(kwargs)
    return instrument_view.focus(**base)


def test_focus_order_and_texts():
    items = focus(position=position(), chains=[chain()],
                  candidates_today=[cand('trend_reclaim', 'buy'), cand('volume_breakout', 'buy')],
                  earnings={'earnings_date': date(2026, 11, 2), 'status': 'known'})
    assert [i['kind'] for i in items] == ['position', 'agent_plan', 'trigger', 'earnings']
    assert items[0]['text'] == '持仓 10 股 · 成本 95.50 · 当前止损 92.00（按你决定的计划）· 距止损 5.4%'
    assert items[0]['link'] == '#review/trades'
    assert items[1]['text'] == ('Agent 第 1 版 · 入场期 · 入场 98.50–101.50 · 止损 96.00 · T1 108.00 · '
                                '最近复查：维持（系统规则，2026-10-27 06:30）')
    assert items[1]['link'] == f'#plan/{HEAD}'
    assert items[2]['text'] == '今天触发 趋势回踩修复（买入）、放量突破（买入）' and items[2]['link'] == '#today'
    assert items[3]['text'] == '7 个交易日后发布财报（2026-11-02）'
    assert all(set(i) == {'kind', 'text', 'link', 'concepts'} for i in items)
    assert 'review.position' in items[0]['concepts'] and 'plan.stop' in items[0]['concepts']
    assert 'agent.version_chain' in items[1]['concepts']
    assert 'rule.trend_reclaim' in items[2]['concepts'] and 'system.decision_unit' in items[2]['concepts']
    assert items[3]['concepts'] == ['plan.earnings']


def test_focus_position_without_plan_and_holding_chain():
    items = focus(position=position(stop=None, plan_id=None, to_stop_pct=None),
                  chains=[chain(mode='holding', state='holding', latest_review=None)])
    assert items[0]['text'] == '持仓 10 股 · 成本 95.50 · 当前止损 无（无计划，按回撤规则）'
    assert items[1]['text'] == 'Agent 第 1 版 · 持仓期 · 入场 98.50–101.50 · 止损 96.00 · T1 108.00 · 尚未复查'
    fractional = focus(position=position(quantity=Decimal('2.5')))
    assert fractional[0]['text'].startswith('持仓 2.5 股')


def test_focus_agent_review_by_agent():
    review = {'outcome': 'revise', 'decided_by': 'agent', 'created_at': datetime(2026, 10, 26, 10, 5, tzinfo=UTC)}
    [item] = focus(chains=[chain(latest_review=review)])
    assert item['text'].endswith('最近复查：修订（Agent，2026-10-26 18:05）')


def test_focus_several_chains_one_item_each():
    items = focus(chains=[chain(), chain(head_plan_id='44444444-4444-4444-8444-444444444444', version_no=0)])
    assert [i['kind'] for i in items] == ['agent_plan', 'agent_plan']
    assert items[1]['text'].startswith('Agent 第 0 版')


def test_focus_conflict_note():
    [item] = focus(candidates_today=[cand('trend_reclaim', 'buy'), cand('trend_break', 'sell')])
    assert item['text'] == '今天触发 趋势回踩修复（买入）、跌破 MA50（卖出）· 同日有买有卖，默认观察'
    [watch] = focus(candidates_today=[cand('new_filing', 'watch')])
    assert watch['text'] == '今天触发 新财报（观察）'
    [only_sell] = focus(candidates_today=[cand('plan_stop', 'sell')])
    assert only_sell['text'] == '今天触发 计划止损（卖出）'


def test_focus_earnings_window():
    def text(earnings):
        items = focus(earnings=earnings)
        return items[0]['text'] if items[0]['kind'] == 'earnings' else None

    assert text({'earnings_date': date(2026, 11, 5), 'status': 'known'}) == '10 个交易日后发布财报（2026-11-05）'
    assert text({'earnings_date': SESSION, 'status': 'known'}) == '今天发布财报（2026-10-22）'
    assert text({'earnings_date': date(2026, 10, 23), 'status': 'known'}) == '1 个交易日后发布财报（2026-10-23）'
    assert text({'earnings_date': date(2026, 11, 6), 'status': 'known'}) is None          # 11 sessions away
    assert text({'earnings_date': date(2026, 10, 21), 'status': 'known'}) is None         # already past
    assert text({'earnings_date': None, 'status': 'unknown'}) is None
    assert text(None) is None


def test_focus_empty_fallback():
    assert focus() == [{'kind': 'none', 'text': NONE_TEXT, 'link': None, 'concepts': []}]
    # an earnings date far away does not count as something to watch
    assert focus(earnings={'earnings_date': date(2027, 1, 20), 'status': 'known'})[0]['text'] == NONE_TEXT


# ---------------------------------------------------------------- key data

def _bars(n, base=100.0):
    start = date(2026, 1, 1)
    return [{'price_date': start + timedelta(days=i), 'open': base + i, 'high': base + i + 1, 'low': base + i - 1,
             'close': base + i, 'adj_close': base + i, 'volume': 1000 + i} for i in range(n)]


def test_key_data_equals_tools_indicators():
    bars, spy = _bars(300), _bars(300, base=400.0)
    out = instrument_view.key_data(bars, spy, as_of=SESSION)
    assert out['as_of'] == '2026-10-22'
    assert out['values'] == tools.indicators(bars, spy)
    assert set(out['values']) == set(out['concepts'])
    assert out['concepts'] == reasoning.TOOL_CONCEPTS['get_indicators']['fields']
    assert out['concepts']['atr14'] == 'indicator.atr14'
    short = instrument_view.key_data(_bars(10), [], as_of=SESSION)
    assert short['values']['ma50'] is None and short['values']['rs20'] is None
    assert instrument_view.key_data([], [], as_of=SESSION)['values']['last_close'] is None


# ---------------------------------------------------------------- context

REGIME = {'label': 'risk_on', 'session': date(2026, 10, 22),
          'sectors': {'SMH': {'state': 'strong', 'rs20': 0.031, 'rs60': 0.052}}}


def test_context_view():
    out = instrument_view.context_view(earnings={'earnings_date': date(2026, 11, 2), 'status': 'known'},
                                       regime=REGIME, sector_etf='SMH', session=SESSION)
    assert out == {'earnings': {'date': '2026-11-02', 'status': 'known', 'sessions_until': 7},
                   'regime': {'label': 'risk_on', 'session': '2026-10-22'},
                   'sector': {'etf': 'SMH', 'state': 'strong', 'rs20': 0.031, 'rs60': 0.052}}


def test_context_view_unmapped_sector():
    out = instrument_view.context_view(earnings=None, regime=None, sector_etf=None, session=SESSION)
    assert out == {'earnings': None, 'regime': None, 'sector': None}
    absent = instrument_view.context_view(earnings=None, regime=REGIME, sector_etf='XLK', session=SESSION)
    assert absent['sector'] == {'etf': 'XLK', 'state': None, 'rs20': None, 'rs60': None}
    unknown = instrument_view.context_view(earnings={'earnings_date': None, 'status': 'unknown'}, regime=None,
                                           sector_etf=None, session=SESSION)
    assert unknown['earnings'] == {'date': None, 'status': 'unknown', 'sessions_until': None}
    past = instrument_view.context_view(earnings={'earnings_date': date(2026, 10, 1), 'status': 'known'}, regime=None,
                                        sector_etf=None, session=SESSION)
    assert past['earnings'] == {'date': '2026-10-01', 'status': 'known', 'sessions_until': None}
