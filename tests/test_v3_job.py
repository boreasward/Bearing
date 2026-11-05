"""The v3 facts job end to end: deterministic compute, persisted runs, the draft file and the CLI codes."""
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.config import AssistantConfig, V3Config
from investment_assistant.core import tx as core_tx
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.__main__ import main
from investment_assistant.facts.job import run_facts

NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)      # 北京 11:30；NYSE 最近收盘 = 2026-11-04（周三）
SESSION = date(2026, 11, 4)
STOCKS = ('AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'INTC')   # 全在纳指 100 范围文件里
BREAKOUTS = ('AAPL', 'MSFT')
SEEN_AT = NOW - timedelta(hours=1)


def cfg_with_briefs(tmp_path):
    return AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path)))


def seed_bars(conn, tickers, *, breakout=()):
    """300 根日线（最后一根是 SESSION）：平静序列，指定股票当天放量突破。"""
    for ticker in tickers:
        for i in range(300):
            close, volume = (110.0, 3_000_000) if i == 299 and ticker in breakout \
                else (100.0 + (i % 5) * 0.05, 1_000_000)
            conn.execute(
                'INSERT INTO price_bars (ticker, price_date, open, high, low, close, adj_close, volume, '
                "source, revision, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'yfinance', 1, %s)",
                (ticker, add_trading_days(SESSION, -(299 - i)), close, close, round(close * 0.99, 2),
                 close, close, volume, SEEN_AT))


def market_ticklers(cfg):
    return tuple(dict.fromkeys(('SPY', 'QQQ', *cfg.v3.sector_etfs, *STOCKS)))   # sector_etfs 首位就是 QQQ


def test_compute_is_deterministic_and_persisted(v3_tx, tmp_path):
    cfg = cfg_with_briefs(tmp_path)
    seed_bars(v3_tx, market_ticklers(cfg), breakout=BREAKOUTS)
    v3_tx.commit()
    r1 = run_facts(now=NOW, collect=False, cfg=cfg)
    r2 = run_facts(now=NOW, collect=False, cfg=cfg)
    assert r1['status'] == r2['status'] == 'ok' and r1['candidates'] == r2['candidates'] >= 2
    assert r1['run_id'] != r2['run_id'] and r1['session'] == r2['session'] == SESSION.isoformat()
    key = lambda rows: {(row['ticker'], row['rank'], row['reason']) for row in rows}
    assert key(facts_repo.candidates(v3_tx, r1['run_id'])) == key(facts_repo.candidates(v3_tx, r2['run_id']))
    run_row = facts_repo.latest_run(v3_tx, SESSION)
    assert run_row is not None and run_row['run_id'] == r2['run_id']
    assert run_row['status'] == 'ok' and run_row['error'] is None
    assert run_row['draft_md'] == Path(r2['draft_path']).read_text(encoding='utf-8')
    assert Path(r2['draft_path']).name == f'{SESSION}.draft.md'
    assert {row['ticker'] for row in facts_repo.candidates(v3_tx, r2['run_id'])} == set(BREAKOUTS)
    assert '→ 进入二次确认' in Path(r2['draft_path']).read_text(encoding='utf-8')


def test_benchmark_missing_writes_error_run(v3_tx, tmp_path):
    cfg = cfg_with_briefs(tmp_path)
    seed_bars(v3_tx, [t for t in market_ticklers(cfg) if t != 'SPY'], breakout=BREAKOUTS)
    v3_tx.commit()
    result = run_facts(now=NOW, collect=False, cfg=cfg)
    assert result['status'] == 'error' and result['candidates'] == 0
    assert result['error'].startswith('基准日线缺失：SPY')
    run_row = facts_repo.latest_run(v3_tx, SESSION)
    assert run_row is not None and run_row['status'] == 'error' and run_row['error'] == result['error']
    assert facts_repo.candidates(v3_tx, result['run_id']) == []
    assert '今天没有草稿：基准日线缺失' in run_row['draft_md']
    assert '今天没有草稿：基准日线缺失' in Path(result['draft_path']).read_text(encoding='utf-8')
    seed_bars(v3_tx, ['SPY'])                      # SPY 补上，改缺 QQQ（screen_rules.BenchmarkMissing）
    v3_tx.execute("DELETE FROM price_bars WHERE ticker = 'QQQ'")
    v3_tx.commit()
    again = run_facts(now=NOW, collect=False, cfg=cfg)
    assert again['status'] == 'error' and again['error'].startswith('基准日线缺失：QQQ')


def test_holdings_and_coverage_reach_the_draft(v3_tx, tmp_path):
    cfg = cfg_with_briefs(tmp_path)
    seed_bars(v3_tx, market_ticklers(cfg), breakout=BREAKOUTS)
    facts_repo.insert_trade(v3_tx, {'request_id': 'buy-1', 'ticker': 'AAPL', 'side': 'buy', 'quantity': 100,
                                    'price': Decimal(95), 'executed_at': NOW - timedelta(days=3)})
    facts_repo.upsert_plan(v3_tx, {
        'session': SESSION, 'ticker': 'AAPL', 'origin': 'brief', 'entry_type': 'breakout_stop',
        'entry_low': Decimal(108), 'entry_high': Decimal(112), 'stop': Decimal(100),
        'target1': Decimal(125), 'levels_source': 'host', 'max_hold_sessions': 10,
        'valid_from': SESSION, 'valid_until': SESSION + timedelta(days=14),
        'thesis': '', 'invalidation': '', 'agent_session': 'facts-job-test'})
    v3_tx.commit()
    result = run_facts(now=NOW, collect=False, cfg=cfg)
    assert result['status'] == 'ok'
    draft = Path(result['draft_path']).read_text(encoding='utf-8')
    holdings = draft.split('## 持仓', 1)[1].split('##', 1)[0]
    assert '- AAPL：100' in holdings and '最新收盘 110' in holdings and '距止损' in holdings
    assert '本次没有采集，用的是库里已有的数据。' in draft       # collect=False 的覆盖率一行（Task 4 parked）


def test_cli_exit_codes(v3_tx, tmp_path, capsys, monkeypatch):
    cfg_path = tmp_path / 'config.json'
    cfg_path.write_text(json.dumps({'v3': {'briefs_dir': str(tmp_path)}}), encoding='utf-8')
    monkeypatch.setenv('INVESTMENT_ASSISTANT_CONFIG', str(cfg_path))
    seed_bars(v3_tx, market_ticklers(cfg_with_briefs(tmp_path)), breakout=BREAKOUTS)
    v3_tx.commit()
    assert main(['run', '--no-collect']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'
    assert main(['show']) == 0
    assert '# 草稿 · 2026-11-04' in capsys.readouterr().out
    v3_tx.execute("DELETE FROM price_bars WHERE ticker = 'SPY'")
    v3_tx.commit()
    assert main(['run', '--no-collect']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'

    def unreachable():
        raise core_tx.Unavailable('数据库未配置')

    monkeypatch.setattr(core_tx, 'connection', unreachable)
    assert main(['run', '--no-collect']) == 2
    err = capsys.readouterr().err
    assert err.startswith('数据库不可用：') and 'Traceback' not in err and err.count('\n') == 1   # 只有一行，无异常栈


def test_cli_show_without_any_run(v3_tx, capsys):
    assert main(['show']) == 1
    assert capsys.readouterr().out.strip() == '还没有运行记录。'
