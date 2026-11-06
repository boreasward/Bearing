"""``python -m investment_assistant.derivation.indicators list / compute <id> <ticker>`` (spec §12.3).

``list`` prints the registry (no database). ``compute`` reads the stored bars as known now and evaluates one
indicator through ``registry.compute`` -- the same function the Agent tool and the daily rules use. Read-only. The
breadth indicator needs the pool's sample, which this CLI does not have, so it refuses it
(``indicator_requires_universe``), and the sector indicator is defined for the sector ETFs only."""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import UTC, datetime
from typing import Any

from investment_assistant.derivation.indicators import registry

BENCHMARKS = {'rs20': 'SPY', 'rs60': 'SPY', 'sector_strength': 'SPY', 'screen_rs': 'QQQ'}


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(_width(cell) for cell in column) for column in zip(headers, *rows)]
    return [('  '.join(cell + ' ' * (w - _width(cell)) for cell, w in zip(row, widths))).rstrip()
            for row in [headers, *rows]]


def list_rows() -> list[list[str]]:
    return [[i.id, i.title, i.basis, '-' if i.window is None else str(i.window), ','.join(i.used_by), i.concept]
            for i in registry.INDICATORS]


def compute_one(indicator_id: str, ticker: str, *, now: datetime) -> dict[str, Any]:
    """``{id, ticker, value, window, basis, version, session, computed_through}`` for the newest closed session."""
    from investment_assistant.core import tx as core_tx
    from investment_assistant.data import identity
    from investment_assistant.data import repo as data_repo
    from investment_assistant.data.adapters.bars import latest_closed_session
    from investment_assistant.data.market_constants import SECTOR_ETFS

    indicator = registry.get(indicator_id)              # LookupError for an unknown id
    ticker = ticker.strip().upper()
    identity.stock_id(ticker)                           # ValueError for an illegal ticker
    if indicator_id == 'breadth':
        raise ValueError('indicator_requires_universe')
    if indicator_id == 'sector_strength' and ticker not in SECTOR_ETFS:
        raise ValueError('indicator_requires_sector_etf')
    session = latest_closed_session(now)
    with core_tx.read_only_tx() as conn:
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now)
        benchmark_id = BENCHMARKS.get(indicator_id)
        benchmark = data_repo.read_bars(conn, benchmark_id, session, cutoff_at=now) if benchmark_id else None
    value = registry.compute(indicator_id, bars, session=session, benchmark_bars=benchmark)
    return {'id': indicator.id, 'ticker': ticker, 'value': value, 'window': indicator.window,
            'basis': indicator.basis, 'version': indicator.version, 'session': session,
            'computed_through': max((row['price_date'] for row in bars), default=None)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.derivation.indicators',
                                     description='指标注册表：列出口径、对一只标的现算一个指标（只读）。')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='列出全部指标（id、标题、价格口径、窗口、使用方、概念卡）')
    compute = commands.add_parser('compute', help='按最近已收盘交易日现算一个指标')
    compute.add_argument('indicator_id', metavar='id', help='指标 id，见 list')
    compute.add_argument('ticker')
    args = parser.parse_args(argv)
    if args.command == 'list':
        for line in _table(['id', '标题', '口径', '窗口', '使用方', '概念'], list_rows()):
            print(line)
        return 0
    from dotenv import load_dotenv

    from investment_assistant.core import tx as core_tx

    load_dotenv()
    try:
        print(json.dumps(compute_one(args.indicator_id, args.ticker, now=datetime.now(UTC)),
                         ensure_ascii=False, default=str))
        return 0
    except (LookupError, ValueError, core_tx.Unavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
