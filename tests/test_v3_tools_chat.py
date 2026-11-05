"""Chat-only tools: confirmed writes of equity and trades, manual family backfill."""
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.data import repo as data_repo
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.dispatch import call
from investment_assistant.toolsurface.env import make_env

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
TRADE = {'ticker': 'AAPL', 'side': 'buy', 'quantity': 10, 'price': 100,
         'executed_at': '2026-11-05T14:00:00+00:00', 'request_id': 't1'}


def _bars(n=20, *, end=SESSION, close=100.0):
    days = [end - timedelta(days=i) for i in range(n)][::-1]
    return [{'price_date': d, 'open': close, 'high': close + 1, 'low': close - 1, 'close': close,
             'adj_close': close, 'volume': 1000, 'split_factor': 1.0} for d in days]


@pytest.fixture
def chat_env(v3_tx):
    facts_repo.insert_run(v3_tx, {'run_id': 'run-1', 'session': SESSION, 'trigger': 'scheduled',
                                  'status': 'ok', 'error': None, 'market': {'label': 'neutral'},
                                  'factors': {}, 'coverage': {}, 'holdings': [], 'draft_md': '# 草稿',
                                  'started_at': NOW - timedelta(minutes=5), 'finished_at': NOW})
    data_repo.store_bars(v3_tx, 'AAPL', _bars(), now=NOW)
    v3_tx.commit()                                              # 事实已入库，写入工具的事务不应带走它
    return make_env(session=SESSION, mode='chat', agent_session='t-chat',
                    cfg=AssistantConfig(), now=NOW)


def test_record_equity_and_trade_need_confirmation(chat_env, v3_tx):
    r = call(chat_env, 'record_equity', {'equity': 50000, 'request_id': 'e1', 'confirmed': False})['data']
    assert r['write'] is False and '50000' in r['preview']
    assert v3_tx.execute('SELECT count(*) FROM v3_equity').fetchone()[0] == 0    # 表里没有行
    row = call(chat_env, 'record_equity', {'equity': 50000, 'request_id': 'e1', 'confirmed': True})['data']
    assert float(row['equity']) == 50000.0
    call(chat_env, 'record_equity', {'equity': 50000, 'request_id': 'e1', 'confirmed': True})  # 重放仍一行
    assert v3_tx.execute('SELECT count(*) FROM v3_equity').fetchone()[0] == 1

    prev = call(chat_env, 'record_trade', {**TRADE, 'confirmed': False})['data']
    assert prev['write'] is False and all(x in prev['preview'] for x in ('AAPL', '买入', '10', '100'))
    assert v3_tx.execute('SELECT count(*) FROM v3_trades').fetchone()[0] == 0
    done = call(chat_env, 'record_trade', {**TRADE, 'confirmed': True})['data']
    assert float(done['quantity']) == 10.0 and done['side'] == 'buy'
    assert v3_tx.execute('SELECT count(*) FROM v3_trades').fetchone()[0] == 1
    # 卖出超过持仓 -> 'position_invalid' 且不写入
    assert call(chat_env, 'record_trade', {'ticker': 'AAPL', 'side': 'sell', 'quantity': 20, 'price': 100,
                                           'executed_at': '2026-11-05T15:00:00+00:00', 'request_id': 't2',
                                           'confirmed': True})['error'].startswith('position_invalid')
    assert v3_tx.execute('SELECT count(*) FROM v3_trades').fetchone()[0] == 1
    # equity <= 0、quantity <= 0、side 不是 buy / sell -> 'invalid_argument'
    assert call(chat_env, 'record_equity', {'equity': 0, 'request_id': 'e2', 'confirmed': True})['error'] \
        .startswith('invalid_argument')
    assert call(chat_env, 'record_trade', {**TRADE, 'request_id': 't3', 'quantity': 0,
                                           'confirmed': True})['error'].startswith('invalid_argument')
    assert call(chat_env, 'record_trade', {**TRADE, 'request_id': 't4', 'side': 'hold',
                                           'confirmed': True})['error'].startswith('invalid_argument')
    # plan_id 指向不存在的计划 -> 'invalid_argument'
    assert call(chat_env, 'record_trade', {**TRADE, 'request_id': 't5', 'plan_id': 999,
                                           'confirmed': True})['error'].startswith('invalid_argument')


def test_ensure_data_calls_collect_extra(chat_env, monkeypatch):
    seen = []

    def fake_collect_extra(tickers, *, session, now, run_id, trigger, cfg, families, deps=None):
        seen.append({'tickers': tickers, 'session': session, 'trigger': trigger, 'families': families})
        return [{'family': 'bars', 'status': 'ok', 'target_count': 1, 'ok_count': 1, 'failures': [],
                 'detail': {}}]

    monkeypatch.setattr(facts_collect, 'collect_extra', fake_collect_extra)
    r = call(chat_env, 'ensure_data', {'ticker': 'NFLX'})['data']
    assert len(seen) == 1 and seen[0]['tickers'] == ['NFLX']
    assert seen[0]['families'] == ('bars', 'news', 'sec_filings', 'earnings', 'fundamentals')
    assert seen[0]['trigger'] == 'manual_family' and seen[0]['session'] == SESSION
    assert r['families'] == {'bars': {'total': 1, 'ok': 1, 'missing': [], 'skipped': None}}
    assert r['lines'] == ['日线：1/1']
    # 非美股代码 -> 'invalid_ticker'，collect_extra 不被调用
    assert call(chat_env, 'ensure_data', {'ticker': '600519.SH'})['error'].startswith('invalid_ticker')
    assert len(seen) == 1
