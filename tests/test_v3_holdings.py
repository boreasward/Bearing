"""v3 holdings view: FIFO positions against latest closes and plan-line rows."""
from datetime import datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.facts import holdings

NY = ZoneInfo('America/New_York')


def trade(tid, ticker, side, qty, price, *, day, hour=15, voided=None):
    return {'id': tid, 'ticker': ticker, 'side': side, 'quantity': D(str(qty)), 'price': D(str(price)),
            'fee': D(0), 'executed_at': datetime(2026, 10, day, hour, tzinfo=NY), 'voided_at': voided}


TRADES = [
    trade(1, 'AAPL', 'buy', 10, 100, day=1),
    trade(2, 'AAPL', 'buy', 10, 110, day=5),
    trade(3, 'MSFT', 'buy', 5, 200, day=2),
    trade(4, 'MSFT', 'sell', 5, 210, day=6),                    # 全部卖出 -> 不出现
    trade(5, 'NVDA', 'buy', 2, 50, day=3,
          voided=datetime(2026, 10, 3, 16, tzinfo=NY)),          # 作废 -> 不计
    trade(6, 'ORCL', 'buy', 4, 60, day=4),
    trade(7, 'IBM', 'buy', 10, 90, day=7),
]

PLANS = {
    'AAPL': {'id': 8, 'ticker': 'AAPL', 'status': 'open', 'kind': 'planned', 'stop': D(99),
             'trailing': False, 'confirmed_stop_order': None, 'held_sessions': 6},
    'ORCL': {'id': 9, 'ticker': 'ORCL', 'status': 'open', 'kind': 'adopted', 'stop': None,
             'trailing': False, 'confirmed_stop_order': None, 'held_sessions': 2},
}

ACTIONS = {'AAPL': {'kind': 'hold', 'text': '继续持有', 'stop': D(99), 'reason': None}}


def test_holdings_view_rows_and_plan_fields():
    rows = holdings.holdings_view(TRADES, closes={'AAPL': 120.0, 'ORCL': 66.0, 'IBM': 90.0},
                                  plans=PLANS, actions=ACTIONS)
    assert [r['ticker'] for r in rows] == ['AAPL', 'IBM', 'ORCL']   # 按代码排序
    aapl = rows[0]
    assert aapl['quantity'] == 20 and aapl['avg_cost'] == 105
    assert aapl['last_close'] == 120.0
    assert aapl['pnl_pct'] == pytest.approx(0.1429, abs=1e-4)
    assert aapl['stop'] == D(99) and aapl['to_stop_pct'] == pytest.approx(0.175)
    # 新字段：计划行 + 调用方传的 held_sessions / next_action / 止损单状态
    assert aapl['plan_id'] == 8 and aapl['kind'] == 'planned'
    assert aapl['trailing'] is False and aapl['held_sessions'] == 6
    assert aapl['next_action'] == ACTIONS['AAPL']
    assert aapl['unrealized_pnl'] == pytest.approx(20 * (120.0 - 105.0))
    assert aapl['stop_order_status'] == 'none'                      # 未确认挂单
    assert aapl['review'] is None                                   # 调用方填
    orcl = rows[2]
    assert orcl['plan_id'] == 9 and orcl['kind'] == 'adopted'
    assert orcl['stop'] is None and orcl['to_stop_pct'] is None
    assert orcl['next_action'] is None and orcl['stop_order_status'] == 'none'
    # 没有计划行的持仓照常出现，计划字段为 None
    ibm = rows[1]
    assert ibm['ticker'] == 'IBM' and ibm['plan_id'] is None and ibm['stop'] is None


def test_stop_order_status_variants():
    plans = {**PLANS, 'AAPL': {**PLANS['AAPL'], 'confirmed_stop_order': D(99)},
             'ORCL': {**PLANS['ORCL'], 'stop': D(60), 'confirmed_stop_order': D(55)}}
    rows = holdings.holdings_view(TRADES, closes={'AAPL': 120.0, 'ORCL': 66.0}, plans=plans)
    by_ticker = {row['ticker']: row for row in rows}
    assert by_ticker['AAPL']['stop_order_status'] == 'confirmed'    # 挂单 = 止损
    assert by_ticker['ORCL']['stop_order_status'] == 'needs_raise'  # 挂单低于止损


def test_holdings_view_fallbacks():
    # 没有最新收盘 -> last_close / pnl_pct / to_stop_pct / unrealized_pnl 均为 None
    ibm = holdings.holdings_view(TRADES, closes={'AAPL': 120.0},
                                 plans={'AAPL': PLANS['AAPL'], 'IBM': {**PLANS['AAPL'], 'id': 10,
                                                                       'ticker': 'IBM'}})[1]
    assert ibm['ticker'] == 'IBM' and ibm['last_close'] is None
    assert ibm['pnl_pct'] is None and ibm['to_stop_pct'] is None
    assert ibm['unrealized_pnl'] is None
    # 卖出数量超过持仓 -> 该股票一行 position_invalid，其他股票照常
    bad = TRADES + [trade(8, 'AAPL', 'sell', 30, 119, day=8)]
    rows2 = holdings.holdings_view(bad, closes={'AAPL': 120.0, 'ORCL': 66.0}, plans={})
    assert {'ticker': 'AAPL', 'error': 'position_invalid'} in rows2
    assert [r['ticker'] for r in rows2 if 'error' not in r] == ['IBM', 'ORCL']
    # 空成交列表 -> []
    assert holdings.holdings_view([], closes={}, plans={}) == []
