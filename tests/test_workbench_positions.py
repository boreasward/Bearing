from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from investment_assistant.execution import positions


def trade(tid, side, qty, price, day, fee='0', hour=15, voided=False):
    return {'id': tid, 'ticker': 'NVDA', 'side': side, 'quantity': D(qty), 'price': D(price), 'fee': D(fee),
            'executed_at': datetime(2026, 10, day, hour, 0, tzinfo=UTC),
            'voided_at': datetime(2026, 10, 30, tzinfo=UTC) if voided else None}


def test_fifo_matches_oldest_lot_first():
    book = positions.build([trade('b1', 'buy', '10', '100', 1), trade('b2', 'buy', '10', '110', 2),
                            trade('s1', 'sell', '15', '120', 3)])
    assert [(s['buy_trade_id'], s['quantity'], s['realized_pnl']) for s in book['closed']] == [
        ('b1', D('10'), D('200')), ('b2', D('5'), D('50'))]
    assert book['open'] == [{'buy_trade_id': 'b2', 'quantity': D('5'), 'cost_per_share': D('110'),
                             'opened_at': datetime(2026, 10, 2, 15, tzinfo=UTC)}]
    assert book['position'] == {'quantity': D('5'), 'avg_cost': D('110'),
                                'opened_at': datetime(2026, 10, 2, 15, tzinfo=UTC)}


def test_fees_go_into_cost_and_reduce_proceeds():
    book = positions.build([trade('b1', 'buy', '10', '100', 1, fee='5'), trade('s1', 'sell', '4', '120', 2, fee='2')])
    seg = book['closed'][0]
    assert seg['entry_cost_per_share'] == D('100.5')
    # proceeds 4*120-2 = 478; cost 4*100.5 = 402
    assert seg['realized_pnl'] == D('76')
    assert book['position']['avg_cost'] == D('100.5')


def test_sell_fee_is_split_across_lots_by_quantity():
    book = positions.build([trade('b1', 'buy', '1', '10', 1), trade('b2', 'buy', '3', '10', 2),
                            trade('s1', 'sell', '4', '10', 3, fee='4')])
    assert [s['realized_pnl'] for s in book['closed']] == [D('-1'), D('-3')]


def test_voided_trades_are_ignored():
    book = positions.build([trade('b1', 'buy', '10', '100', 1), trade('s1', 'sell', '10', '90', 2, voided=True)])
    assert book['closed'] == [] and book['position']['quantity'] == D('10')


def test_split_rescales_open_lots_before_later_trades():
    # 4-for-1 split effective 2026-10-05: 10 @ 100 becomes 40 @ 25.
    book = positions.build([trade('b1', 'buy', '10', '100', 1), trade('s1', 'sell', '20', '30', 6)],
                           splits=[(date(2026, 10, 5), D('4'))])
    seg = book['closed'][0]
    assert seg['quantity'] == D('20') and seg['entry_cost_per_share'] == D('25')
    assert seg['realized_pnl'] == D('100')
    assert book['position'] == {'quantity': D('20'), 'avg_cost': D('25'),
                                'opened_at': datetime(2026, 10, 1, 15, tzinfo=UTC)}


def test_split_after_all_trades_still_rescales_current_position():
    book = positions.build([trade('b1', 'buy', '10', '100', 1)], splits=[(date(2026, 10, 5), D('2'))])
    assert book['position']['quantity'] == D('20') and book['position']['avg_cost'] == D('50')


def test_split_on_trade_day_applies_before_that_trade():
    # Trades carry the NY session date; a split effective that day precedes them.
    book = positions.build([trade('b1', 'buy', '10', '100', 1), trade('b2', 'buy', '1', '30', 5)],
                           splits=[(date(2026, 10, 5), D('4'))])
    assert book['position']['quantity'] == D('41')


def test_selling_more_than_held_is_an_error():
    with pytest.raises(positions.PositionError):
        positions.build([trade('b1', 'buy', '1', '100', 1), trade('s1', 'sell', '2', '100', 2)])


def test_empty_book_has_no_position():
    assert positions.build([]) == {'closed': [], 'open': [], 'position': None}


def test_trades_are_processed_in_execution_order_not_input_order():
    book = positions.build([trade('s1', 'sell', '5', '120', 3), trade('b1', 'buy', '5', '100', 1)])
    assert book['closed'][0]['sell_trade_id'] == 's1'
