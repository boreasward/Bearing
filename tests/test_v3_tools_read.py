"""Read tools over the v3_tx database: stored facts, env-fixed cutoff, error envelopes."""
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.dispatch import call, tool_defs
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)                                   # 周三
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)                # 数据截止时点
TOP5 = ['AAPL', 'MSFT', 'NVDA', 'MU', 'AVGO']


def _bars(n=20, *, end=SESSION, close=100.0):
    """n 根日线（含 end 当天），真实波幅恒为 2，最新收盘 close。"""
    days = [end - timedelta(days=i) for i in range(n)][::-1]
    return [{'price_date': d, 'open': close, 'high': close + 1, 'low': close - 1, 'close': close,
             'adj_close': close, 'volume': 1000, 'split_factor': 1.0} for d in days]


def _run_row(run_id='run-1'):
    return {'run_id': run_id, 'session': SESSION, 'trigger': 'scheduled', 'status': 'ok', 'error': None,
            'market': {'label': 'neutral', 'regime_tier': '中性', 'breadth': 0.5, 'spy_ret20': 0.01,
                       'etf_table': [{'etf': 'XLK', 'ret20': 0.02}], 'macro': {'DGS10': 0.04}},
            'factors': {}, 'coverage': {'families': {'bars': {'total': 5, 'ok': 5, 'missing': [],
                                                              'skipped': None}},
                                        'missing_bars': []},
            'holdings': [], 'draft_md': '# 草稿',
            'started_at': NOW - timedelta(minutes=5), 'finished_at': NOW}


def _candidate(ticker, rank):
    return {'ticker': ticker, 'rank': rank, 'screen_group': 'trigger', 'trigger_rules': ['pullback'],
            'rs': 1.5, 'atr_pct': 2.0, 'setup_tier': 'favorable', 'industry': '半导体',
            'industry_tier': 'favorable', 'reason': f'{ticker} 的理由行'}


@pytest.fixture
def env(v3_tx):
    facts_repo.insert_run(v3_tx, _run_row())
    facts_repo.insert_candidates(v3_tx, 'run-1', [_candidate(t, i + 1) for i, t in enumerate(TOP5)]
                                 + [_candidate('AMD', 6)])          # 第 6 只不在前 N
    for ticker in TOP5:
        data_repo.store_bars(v3_tx, ticker, _bars(), now=NOW)
    data_repo.store_news(v3_tx, [({'source': 'yfinance', 'title': f'新闻 {i}',
                                   'url': f'https://n.example/{i}',
                                   'published_at': NOW - timedelta(hours=i)}, ['AAPL'])
                                 for i in range(25)], now=NOW)
    for i in range(25):
        v3_tx.execute('INSERT INTO filings (ticker, form, filing_date, accession_number, '
                      'primary_document, file_path, accepted_at, items, created_at) '
                      "VALUES (%s, '10-Q', %s, %s, %s, %s, %s, %s, %s)",
                      ('AAPL', SESSION - timedelta(days=i), f'acc-{i:03d}', f'doc-{i}.htm', '/tmp/f',
                       NOW - timedelta(days=i), ['Item 2'], NOW - timedelta(days=i)))
    v3_tx.commit()                                              # 事实已入库，工具的事务回滚不应带走它
    return make_env(session=SESSION, mode='brief', agent_session='t-read',
                    cfg=AssistantConfig(), now=NOW)


def test_read_tools_return_stored_facts(env, v3_tx):
    out = [call(env, 'get_market', {}), call(env, 'get_candidates', {}),
           call(env, 'get_price_bars', {'ticker': 'aapl', 'lookback': 3})]
    assert [o['ref'] for o in out] == ['call:1', 'call:2', 'call:3']     # ref 递增
    assert out[0]['data']['label'] == 'neutral'
    assert out[0]['data']['session'] == SESSION.isoformat()
    cands = out[1]['data']
    assert [c['ticker'] for c in cands['candidates'] if c['in_top_n']] == TOP5 and cands['top_n'] == 5
    assert cands['candidates'][0]['verdict'] is None                     # 还没有结论
    assert len(out[2]['data']) == 3
    assert out[2]['data'][-1] == {'date': SESSION.isoformat(), 'open': 100.0, 'high': 101.0,
                                  'low': 99.0, 'close': 100.0, 'volume': 1000}
    assert len(call(env, 'get_news', {'ticker': 'AAPL'})['data']) == 20  # 各自不超过 20 条
    assert len(call(env, 'get_filings', {'ticker': 'AAPL'})['data']) == 20
    assert call(env, 'get_earnings', {'ticker': 'AAPL'})['data'] is None  # 没有数据返回 None
    assert len(call(env, 'get_positions', {})['data']) == 0
    assert v3_tx.execute('SELECT count(*) FROM v3_tool_calls').fetchone()[0] == 7   # 每次调用一行


def test_cutoff_and_session_are_fixed_by_env(env, v3_tx):
    data_repo.store_bars(v3_tx, 'AAPL', _bars(1, end=SESSION + timedelta(days=1)), now=NOW)
    later = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)
    data_repo.store_bars(v3_tx, 'AAPL', [{'price_date': SESSION, 'open': 100.0, 'high': 101.0,
                                          'low': 99.0, 'close': 90.0, 'adj_close': 90.0,
                                          'volume': 1, 'split_factor': 1.0}], now=later)
    bars = call(env, 'get_price_bars', {'ticker': 'AAPL', 'lookback': 60})['data']
    assert bars[-1]['date'] == SESSION.isoformat()                       # 最后一根仍是 env.session
    assert bars[-1]['close'] == 100.0                                    # 晚于 cutoff 的修订不被读到
    assert len(bars) == 20                                               # 之后一天的日线也不出现


def test_bad_arguments_return_error_envelope(env, v3_tx):
    errs = [call(env, 'nope', {}),
            call(env, 'get_price_bars', {'ticker': 'AAPL', 'lookback': 100000}),
            call(env, 'get_price_bars', {'ticker': 'aa pl'}),
            call(env, 'get_price_bars', {'ticker': 'AAPL', 'extra': 1}),
            call(env, 'get_price_bars', {})]
    assert errs[0]['error'].startswith('unknown_tool')
    assert errs[1]['error'].startswith('invalid_argument')
    assert errs[2]['error'].startswith('invalid_ticker')
    assert errs[3]['error'].startswith('unknown_argument')
    assert errs[4]['error'].startswith('missing_argument')
    assert call(env, 'get_price_bars', {'ticker': 'ZZZZ'})['data'] == []  # 没有日线不算错误
    assert call(env, 'get_indicators', {'ticker': 'ZZZZ'})['error'].startswith('no_bars')
    rows = v3_tx.execute('SELECT error FROM v3_tool_calls ORDER BY seq').fetchall()
    assert len(rows) == 7                                                # 每次调用（含出错的）记一行
    assert all(r[0] for r in rows[:5])                                  # 出错的调用 error 列有值
    assert call(env, 'get_market', {})['data']['label'] == 'neutral'     # 之后的正常调用照常成功


def test_chat_only_tools_hidden_in_brief(env):
    assert 'record_trade' not in {t.name for t in tool_defs('brief')}
    assert 'record_trade' in {t.name for t in tool_defs('chat')}
    assert call(env, 'record_equity', {'equity': 1, 'request_id': 'r', 'confirmed': True})['error'] \
        .startswith('not_available_in_mode')


def test_no_run_for_session(v3_tx):
    env = make_env(session=SESSION, mode='brief', agent_session='t-none',
                   cfg=AssistantConfig(), now=NOW)
    assert call(env, 'get_market', {})['error'].startswith('no_run')
    assert call(env, 'get_candidates', {})['error'].startswith('no_run')
    assert call(env, 'get_coverage', {})['error'].startswith('no_run')
