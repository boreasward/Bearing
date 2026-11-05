"""v3 holdings view: FIFO positions against latest closes and stops."""
from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.facts import holdings

NY = ZoneInfo('America/New_York')


def trade(tid, ticker, side, qty, price, *, day, hour=15, voided=None):
    return {'id': tid, 'ticker': ticker, 'side': side, 'quantity': D(str(qty)), 'price': D(str(price)),
            'fee': D(0), 'executed_at': datetime(2026, 10, day, hour, tzinfo=NY), 'voided_at': voided}


def test_holdings_view():
    trades = [
        trade(1, 'AAPL', 'buy', 10, 100, day=1),
        trade(2, 'AAPL', 'buy', 10, 110, day=5),
        trade(3, 'MSFT', 'buy', 5, 200, day=2),
        trade(4, 'MSFT', 'sell', 5, 210, day=6),                    # 全部卖出 -> 不出现
        trade(5, 'NVDA', 'buy', 2, 50, day=3,
              voided=datetime(2026, 10, 3, 16, tzinfo=NY)),          # 作废 -> 不计
        trade(6, 'ORCL', 'buy', 4, 60, day=4),
        trade(7, 'IBM', 'buy', 10, 90, day=7),
    ]
    rows = holdings.holdings_view(trades, closes={'AAPL': 120.0, 'ORCL': 66.0, 'IBM': 90.0},
                                  stops={'AAPL': D(99), 'ORCL': None})
    assert [r['ticker'] for r in rows] == ['AAPL', 'IBM', 'ORCL']   # 按代码排序
    aapl = rows[0]
    assert aapl['quantity'] == 20 and aapl['avg_cost'] == 105
    assert aapl['last_close'] == 120.0
    assert aapl['pnl_pct'] == pytest.approx(0.1429, abs=1e-4)
    assert aapl['stop'] == D(99) and aapl['to_stop_pct'] == pytest.approx(0.175)
    orcl = rows[2]
    assert orcl['stop'] is None and orcl['to_stop_pct'] is None
    assert orcl['pnl_pct'] == pytest.approx(0.1)
    # 没有最新收盘 -> last_close / pnl_pct / to_stop_pct 均为 None，其余照常
    ibm = holdings.holdings_view(trades, closes={'AAPL': 120.0}, stops={'AAPL': D(99), 'IBM': D(80)})[1]
    assert ibm['ticker'] == 'IBM' and ibm['last_close'] is None
    assert ibm['pnl_pct'] is None and ibm['to_stop_pct'] is None
    # 卖出数量超过持仓 -> 该股票一行 position_invalid，其他股票照常
    bad = trades + [trade(8, 'AAPL', 'sell', 30, 119, day=8)]
    rows2 = holdings.holdings_view(bad, closes={'AAPL': 120.0, 'ORCL': 66.0}, stops={})
    assert {'ticker': 'AAPL', 'error': 'position_invalid'} in rows2
    assert [r['ticker'] for r in rows2 if 'error' not in r] == ['IBM', 'ORCL']
    # 空成交列表 -> []
    assert holdings.holdings_view([], closes={}, stops={}) == []
