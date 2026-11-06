"""Plan tools: host-validated verdicts and plans, host arithmetic for levels and sizing."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.dispatch import call
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)                                   # 周三；T+1..T+3 = 11/5、11/6、11/9
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
TOP5 = ['AAPL', 'MSFT', 'NVDA', 'MU', 'AVGO']
TEXTS = {'thesis': '回调到 99 附近买入', 'invalidation': '跌破 96 止损'}


def _bars(n=20, *, end=SESSION, close=100.0):
    days = [end - timedelta(days=i) for i in range(n)][::-1]
    return [{'price_date': d, 'open': close, 'high': close + 1, 'low': close - 1, 'close': close,
             'adj_close': close, 'volume': 1000, 'split_factor': 1.0} for d in days]


def _run_row(run_id='run-1'):
    return {'run_id': run_id, 'session': SESSION, 'trigger': 'scheduled', 'status': 'ok', 'error': None,
            'market': {'label': 'neutral'}, 'factors': {}, 'coverage': {}, 'holdings': [],
            'draft_md': '# 草稿', 'started_at': NOW - timedelta(minutes=5), 'finished_at': NOW}


def _candidate(ticker, rank):
    return {'ticker': ticker, 'rank': rank, 'screen_group': 'trigger', 'trigger_rules': ['pullback'],
            'rs': 1.5, 'atr_pct': 2.0, 'setup_tier': 'favorable', 'industry': '半导体',
            'industry_tier': 'favorable', 'reason': f'{ticker} 的理由行'}


def _seed(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())
    facts_repo.insert_candidates(v3_tx, 'run-1', [_candidate(t, i + 1) for i, t in enumerate(TOP5)])
    for ticker in TOP5:
        data_repo.store_bars(v3_tx, ticker, _bars(), now=NOW)
    v3_tx.commit()                                              # 事实已入库，保存被拒的回滚不应带走它


def _env(v3_tx, mode='brief', agent='t-plan'):
    return make_env(session=SESSION, mode=mode, agent_session=agent,
                    cfg=AssistantConfig(), now=NOW)


def confirm(env, ticker, verdict='confirm'):
    """保存一条已确认结论；先做一次真实读调用，refs 指向它的 ref。"""
    ref = call(env, 'get_market', {})['ref']
    r = call(env, 'save_verdict', {'ticker': ticker, 'verdict': verdict, 'reasons': '理由充分，形态与行业共振',
                                   'refs': [ref]})
    assert 'data' in r, r
    return r


def test_compute_plan_levels_uses_host_arithmetic(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    d = call(env, 'compute_plan_levels', {'ticker': TOP5[0]})['data']
    assert d['ticker'] == 'AAPL' and d['session'] == SESSION.isoformat()
    assert d['levels'] == {'entry_low': '99.00', 'entry_high': '101.00', 'stop': '96.00', 'target1': '108.00'}
    assert d['sizing']['status'] == 'no_equity' and d['sizing']['shares'] is None
    assert d['entry_types'] == ['zone_limit', 'breakout_stop', 'next_open'] and d['max_hold_range'] == [3, 30]


def test_save_plan_check_order_scope_and_verdict_first(v3_tx):
    """钉住检查顺序：范围 → 确认结论 → 入场类型 → 最长持有（spec §6）。"""
    _seed(v3_tx)
    env = _env(v3_tx, agent='t-order')
    # 名单外标的带非法入场类型 -> 先报范围，不报 invalid_entry_type
    assert call(env, 'save_plan', {'ticker': 'ZZZZ', **TEXTS, 'entry_type': 'market'})['error'] \
        .startswith('out_of_scope')
    # 名单内但未确认的标的带越界最长持有 -> 先报没有确认结论，不报 invalid_max_hold
    assert call(env, 'save_plan', {'ticker': TOP5[0], **TEXTS, 'max_hold_sessions': 40})['error'] \
        .startswith('no_confirm_verdict')


def test_save_plan_rejections(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    t = TOP5[0]
    assert call(env, 'save_plan', {'ticker': 'ZZZZ', **TEXTS})['error'].startswith('out_of_scope')
    assert call(env, 'save_plan', {'ticker': t, **TEXTS})['error'].startswith('no_confirm_verdict')
    confirm(env, t)                                             # 这次保存本身记为 call:3
    assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'shares': 100})['error'].startswith('shares_not_allowed')
    assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'quantity': 100})['error'] \
        .startswith('shares_not_allowed')
    assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'entry_type': 'market'})['error'] \
        .startswith('invalid_entry_type')
    assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'max_hold_sessions': 40})['error'] \
        .startswith('invalid_max_hold')
    bad = {'entry_low': 99, 'entry_high': 101, 'stop': 99.5, 'target1': 108}   # 止损不在入场下方
    assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'levels': bad})['error'].startswith('levels_invalid')
    assert v3_tx.execute('SELECT count(*) FROM v3_plans').fetchone()[0] == 0    # 每次之后 v3_plans 仍为空


def test_save_plan_host_and_agent_levels(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    t = TOP5[0]
    confirm(env, t)
    host = call(env, 'compute_plan_levels', {'ticker': t})['data']
    saved = call(env, 'save_plan', {'ticker': t, **TEXTS})['data']
    assert saved['levels_source'] == 'host'
    assert {k: D(str(saved[k])) for k in ('entry_low', 'entry_high', 'stop', 'target1')} \
        == {k: D(v) for k, v in host['levels'].items()}          # 价位等于 compute_plan_levels 的结果
    agent_levels = {'entry_low': 99.5, 'entry_high': 100.5, 'stop': 97, 'target1': 106}
    again = call(env, 'save_plan', {'ticker': t, **TEXTS, 'levels': agent_levels})['data']
    assert again['levels_source'] == 'agent' and float(again['stop']) == 97.0
    assert v3_tx.execute('SELECT count(*) FROM v3_plans').fetchone()[0] == 1     # 覆盖同一行
    # 财报日落在有效期内 -> 'earnings_in_validity'
    data_repo.store_earnings(v3_tx, TOP5[1], {'status': 'known', 'earnings_date': date(2026, 11, 6)}, now=NOW)
    confirm(env, TOP5[1])
    assert call(env, 'save_plan', {'ticker': TOP5[1], **TEXTS})['error'].startswith('earnings_in_validity')


def test_plan_without_equity_and_without_room(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx)
    t = TOP5[0]
    confirm(env, t)
    saved = call(env, 'save_plan', {'ticker': t, **TEXTS})['data']
    assert saved['shares'] is None and '未提供账户资金，未算股数' in saved['warnings']
    facts_repo.insert_equity(v3_tx, {'request_id': 'eq-1', 'as_of': SESSION, 'equity': D(100000), 'note': ''})
    again = call(env, 'save_plan', {'ticker': t, **TEXTS})['data']
    # 按风险 1% = 1000、每股风险 4 -> 250 股；单票上限 25%：25000 / 101 取整 247 -> 取较小者
    assert again['shares'] == 247 and again['sizing']['status'] == 'capped'
    assert again['sizing']['applied_cap'] == 'single'
    # 另一只股票已有 1000 股、最新收盘 100（市值等于本金，占满总仓位上限）
    facts_repo.insert_trade(v3_tx, {'request_id': 'tr-1', 'ticker': TOP5[1], 'side': 'buy', 'quantity': 1000,
                                    'price': D(100), 'executed_at': datetime(2026, 11, 3, 15, tzinfo=UTC)})
    third = call(env, 'save_plan', {'ticker': t, **TEXTS})['data']
    assert third['shares'] == 0 and 'no_room' in third['warnings']
    # 持仓再涨到超过本金（110000 > 100000）：仍是 0 股，不是负数
    facts_repo.insert_trade(v3_tx, {'request_id': 'tr-2', 'ticker': TOP5[1], 'side': 'buy', 'quantity': 100,
                                    'price': D(100), 'executed_at': datetime(2026, 11, 3, 16, tzinfo=UTC)})
    over = call(env, 'save_plan', {'ticker': t, **TEXTS})['data']
    assert over['shares'] == 0 and over['sizing']['status'] == 'no_room' and 'no_room' in over['warnings']
    levels = call(env, 'compute_plan_levels', {'ticker': t})['data']
    assert levels['sizing']['shares'] == 0 and levels['sizing']['status'] == 'no_room'


def test_verdict_rules(v3_tx):
    _seed(v3_tx)
    env = _env(v3_tx, agent='t-verdict')
    t = TOP5[0]
    assert call(env, 'save_verdict', {'ticker': t, 'verdict': 'maybe', 'reasons': 'r', 'refs': []})['error'] \
        .startswith('invalid_verdict')
    assert call(env, 'save_verdict', {'ticker': t, 'verdict': 'unknown', 'reasons': '', 'refs': []})['error'] \
        .startswith('invalid_argument')
    assert call(env, 'save_verdict', {'ticker': t, 'verdict': 'unknown', 'reasons': 'x' * 2001,
                                      'refs': []})['error'].startswith('invalid_argument')
    call(env, 'get_market', {})                                 # 记为 call:4，给下面的 refs 用
    assert call(env, 'save_verdict', {'ticker': t, 'verdict': 'confirm', 'reasons': '理由',
                                      'refs': ['call:99']})['error'].startswith('invalid_refs')
    assert call(env, 'save_verdict', {'ticker': t, 'verdict': 'confirm', 'reasons': '理由',
                                      'refs': []})['error'].startswith('invalid_refs')
    assert 'data' in call(env, 'save_verdict', {'ticker': t, 'verdict': 'unknown', 'reasons': '理由',
                                                'refs': []})          # unknown 可以没有 ref
    second = call(env, 'save_verdict', {'ticker': t, 'verdict': 'veto', 'reasons': '第二次的理由',
                                        'refs': ['call:4']})['data']
    assert v3_tx.execute('SELECT count(*) FROM v3_verdicts').fetchone()[0] == 1    # 同一标的一行
    assert second['verdict'] == 'veto' and second['reasons'] == '第二次的理由'      # 内容是第二次的


def test_brief_scope_empty_top_n(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())                   # 运行里没有候选
    env = _env(v3_tx)
    assert call(env, 'save_verdict', {'ticker': 'AAPL', 'verdict': 'unknown', 'reasons': 'r'})['error'] \
        .startswith('out_of_scope')
    assert call(env, 'save_plan', {'ticker': 'AAPL', **TEXTS})['error'].startswith('out_of_scope')


def test_chat_mode_allows_named_ticker(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())
    facts_repo.insert_candidates(v3_tx, 'run-1', [_candidate('AAPL', 1)])
    data_repo.store_bars(v3_tx, 'MSFT', _bars(), now=NOW)      # 名单外但有日线
    v3_tx.commit()
    env = _env(v3_tx, mode='chat', agent='t-chat')
    assert call(env, 'save_verdict', {'ticker': 'MSFT', 'verdict': 'unknown', 'reasons': 'r'})['data'] \
        ['origin'] == 'chat'
    assert 'data' in call(env, 'save_verdict', {'ticker': 'MSFT', 'verdict': 'confirm', 'reasons': 'r',
                                                'refs': ['call:1']})
    plan = call(env, 'save_plan', {'ticker': 'MSFT', **TEXTS})['data']
    assert plan['origin'] == 'chat' and float(plan['entry_low']) == 99.0
