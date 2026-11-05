"""Read tools: the day's facts, all cut at the env's session and cutoff (spec §6 读)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from investment_assistant.core.constants import BENCHMARK
from investment_assistant.core.knowledge import agent_text, catalog
from investment_assistant.data import repo as data_repo
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.coverage import coverage_lines
from investment_assistant.facts.holdings import holdings_view
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import normalize_ticker

NEWS_LIMIT = 20
FILINGS_LIMIT = 20
FUNDAMENTALS_LIMIT = 40


def _as_int(value: Any) -> int | None:
    """真整数（不含 bool）才返回；其余输入归 None，由调用方走错误信封。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_arg(value: Any, name: str, lo: int, hi: int) -> int:
    number = _as_int(value)
    if number is None or not lo <= number <= hi:
        raise ValueError(f'invalid_argument: {name} 必须是 {lo}–{hi} 之间的整数')
    return number


def latest_run_or_none(conn, env: ToolEnv) -> dict | None:
    return facts_repo.latest_run(conn, env.session)


def need_run(conn, env: ToolEnv) -> dict:
    run = latest_run_or_none(conn, env)
    if run is None:
        raise ValueError('no_run')
    return run


def top_tickers(conn, env: ToolEnv) -> list[str]:
    """当期名单前 N 只；没有运行记录时为空（brief 模式下任何标的都不在范围内）。"""
    run = latest_run_or_none(conn, env)
    if run is None:
        return []
    return [row['ticker'] for row in facts_repo.candidates(conn, run['run_id'])
            if row['rank'] <= env.cfg.v3.top_n]


def session_bars(conn, env: ToolEnv, ticker: str) -> list[dict]:
    return data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at)


def holdings_rows(conn, env: ToolEnv) -> list[dict]:
    """该交易日口径下的持仓视图：记录在案的成交、最新收盘与最近计划里的止损。"""
    trades = facts_repo.list_trades(conn)
    closes, stops, splits = {}, {}, {}
    for ticker in sorted({trade['ticker'] for trade in trades}):
        bars = session_bars(conn, env, ticker)
        if bars:
            closes[ticker] = bars[-1]['close']
        plan = facts_repo.latest_plan(conn, ticker)
        stops[ticker] = plan['stop'] if plan is not None else None
        splits[ticker] = data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at)
    return holdings_view(trades, closes=closes, stops=stops, splits=splits)


def get_market(env: ToolEnv) -> dict:
    def _read(conn):
        run = need_run(conn, env)
        market = run['market'] or {}
        return {'session': run['session'], 'label': market.get('label'),
                'regime_tier': market.get('regime_tier'), 'breadth': market.get('breadth'),
                'spy_ret20': market.get('spy_ret20'), 'etf_table': market.get('etf_table'),
                'macro': market.get('macro')}
    return env.read(_read)


def get_candidates(env: ToolEnv) -> dict:
    def _read(conn):
        run = need_run(conn, env)
        verdicts = facts_repo.verdicts(conn, env.session)
        rows = [{'ticker': row['ticker'], 'rank': row['rank'], 'reason': row['reason'],
                 'screen_group': row['screen_group'], 'trigger_rules': row['trigger_rules'],
                 'setup_tier': row['setup_tier'], 'industry': row['industry'],
                 'industry_tier': row['industry_tier'], 'rs': row['rs'],
                 'in_top_n': row['rank'] <= env.cfg.v3.top_n,
                 'verdict': verdicts[row['ticker']]['verdict'] if row['ticker'] in verdicts else None}
                for row in facts_repo.candidates(conn, run['run_id'])]
        return {'run_id': run['run_id'], 'top_n': env.cfg.v3.top_n, 'candidates': rows}
    return env.read(_read)


def get_coverage(env: ToolEnv) -> dict:
    def _read(conn):
        run = need_run(conn, env)
        cov = run['coverage'] or {}
        families = cov.get('families') or {}
        missing = cov.get('missing_bars') or []
        return {'families': families, 'missing_bars': missing,
                'lines': coverage_lines({'families': families, 'missing_bars': missing})}
    return env.read(_read)


def get_price_bars(env: ToolEnv, *, ticker: str, lookback: int = 60) -> list[dict]:
    ticker = normalize_ticker(ticker)
    lookback = _int_arg(lookback, 'lookback', 1, 120)

    def _read(conn):
        rows = data_repo.read_bars(conn, ticker, env.session, cutoff_at=env.cutoff_at, limit=lookback)
        return [{'date': row['price_date'], 'open': row['open'], 'high': row['high'], 'low': row['low'],
                 'close': row['close'], 'volume': row['volume']} for row in rows]
    return env.read(_read)


def get_indicators(env: ToolEnv, *, ticker: str) -> dict:
    ticker = normalize_ticker(ticker)

    def _read(conn):
        bars = session_bars(conn, env, ticker)
        if not bars:
            raise ValueError('no_bars')
        spy = session_bars(conn, env, BENCHMARK)
        return price_indicators.indicators(bars, spy or [])
    return env.read(_read)


def get_news(env: ToolEnv, *, ticker: str, days: int = 7) -> list[dict]:
    ticker = normalize_ticker(ticker)
    days = _int_arg(days, 'days', 1, 14)

    def _read(conn):
        rows = data_repo.read_news(conn, ticker, cutoff_at=env.cutoff_at,
                                   since=env.cutoff_at - timedelta(days=days), limit=NEWS_LIMIT)
        return [{'source': row['source'], 'title': row['title'], 'url': row['url'],
                 'published_at': row['published_at']} for row in rows]
    return env.read(_read)


def get_filings(env: ToolEnv, *, ticker: str, days: int = 30) -> list[dict]:
    ticker = normalize_ticker(ticker)
    days = _int_arg(days, 'days', 1, 90)

    def _read(conn):
        rows = data_repo.read_filings(conn, ticker, since=env.session - timedelta(days=days),
                                      cutoff_at=env.cutoff_at)
        return [{'form': row['form'], 'filing_date': row['filing_date'], 'accepted_at': row['accepted_at'],
                 'items': row['items'], 'accession_number': row['accession_number']}
                for row in rows[:FILINGS_LIMIT]]
    return env.read(_read)


def get_earnings(env: ToolEnv, *, ticker: str) -> dict | None:
    ticker = normalize_ticker(ticker)

    def _read(conn):
        row = data_repo.latest_earnings(conn, ticker, cutoff_at=env.cutoff_at)
        return None if row is None else {'earnings_date': row['earnings_date'], 'status': row['status']}
    return env.read(_read)


def get_fundamentals(env: ToolEnv, *, ticker: str) -> dict:
    ticker = normalize_ticker(ticker)
    return env.read(lambda conn: {'rows': data_repo.financial_quarters(
        conn, ticker, cutoff_at=env.cutoff_at)[:FUNDAMENTALS_LIMIT]})


def get_positions(env: ToolEnv) -> list[dict]:
    return env.read(lambda conn: holdings_rows(conn, env))


def get_plans(env: ToolEnv, *, session: str | None = None) -> dict:
    target = env.session if session is None else _session_arg(session)

    def _read(conn):
        return {'plans': facts_repo.plans(conn, target), 'verdicts': facts_repo.verdicts(conn, target)}
    return env.read(_read)


def _session_arg(value: str) -> date:
    text = value if isinstance(value, str) else ''
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError('invalid_argument: session 必须是 YYYY-MM-DD') from None


def get_concept(env: ToolEnv, *, concept_id: str | None = None) -> dict:
    if concept_id is not None and not isinstance(concept_id, str):
        raise ValueError('invalid_argument: concept_id 必须是字符串')
    return agent_text(catalog(), concept_id or None)
