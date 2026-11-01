"""One session of paper settlement (`paper-fill-1`); no reads, writes, or model calls.

Basis contract: stored bars, PositionState prices and holdings.quantity use the same
current split basis (holdings from positions.build). Pending shares use placed_on's
basis. Fills are converted back to the execution day's basis so positions.build can
apply split events exactly once. Execution timestamps enforce simulated FIFO order,
not actual intraday timing (same-day later legs can be one microsecond apart). The caller must rescale persisted state when a new
split becomes known: persist its applied cumulative factor from plan.signal_date,
then divide its price fields by new_factor / old_factor; quantities come from book.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR
from zoneinfo import ZoneInfo

from investment_assistant.core.texts import (CANCEL_REASON_TEXT as CANCEL_REASON_TEXT,
                                             CHAIN_DONE_TEXT as CHAIN_DONE_TEXT,
                                             VOID_REASON_TEXT as VOID_REASON_TEXT)
from investment_assistant.core.versions import PAPER_FILL_VERSION as PAPER_FILL_VERSION
from investment_assistant.execution import simulation as plan_shadow
from investment_assistant.execution import entry_rules
from investment_assistant.execution.simulation import PositionState, step_position
from investment_assistant.execution.plan_levels import MAX_HOLD_DEFAULT

FILL_CASES = ('in_zone', 'gap_up_limit', 'gap_down_stop_buy')
EXIT_REASONS = ('stop', 'gap_stop', 'target1_half', 'time_stop', 'agent_sell', 'exit_instruction')
EXIT_REASON_TEXT = {'stop': '止损触及', 'gap_stop': '跳空止损', 'target1_half': '到 T1 卖出一半',
                    'time_stop': '时间止损', 'agent_sell': 'Agent 卖出', 'exit_instruction': '执行退出指令'}
FILL_CASE_TEXT = {'in_zone': '区间内成交', 'gap_up_limit': '高开回落限价成交', 'gap_down_stop_buy': '低开回升触发成交'}
NY = ZoneInfo('America/New_York')
_DECIMAL_FIELDS = ('fill_price', 'stop', 'stop0', 'risk', 'highest_close')


def _half(qty: Decimal, factor: Decimal) -> Decimal:
    """Half of the position in whole shares of the execution day's basis, returned in the current basis.

    ``qty`` is current-basis; ``factor`` converts it back to the session's basis (as ``fill`` does), so a
    split known only later never produces half shares or swallows a sale. A lone share is never sold.
    """
    return (qty / factor / 2).to_integral_value(rounding=ROUND_FLOOR) * factor


def _state(value: PositionState | dict) -> PositionState:
    if isinstance(value, PositionState):
        return value
    values = dict(value)
    for key in _DECIMAL_FIELDS:
        if values.get(key) is not None:
            values[key] = Decimal(str(values[key]))
    return PositionState(**values)


def settle_day(*, session: date, account: dict, bars_by_ticker: dict, pending_orders: list,
               holdings: list, plans_by_id: dict, splits_by_ticker: dict) -> dict:
    """Settle pending orders placed before session and observed bars only.

    Holding rows carry ticker, quantity, frozen plan_id, state and optionally
    chain_signal_date / agent_sell / exit_instruction. `states` contains only
    positions still open, unchanged on a missing bar. Automatic exits remain active
    on the remainder after an Agent half-sale. No same-day order can fill.
    """
    result = {'fills': [], 'order_updates': [], 'chain_done': [], 'states': {}, 'bars_missing': []}
    fee = Decimal(str((account.get('params') or {}).get('fee_per_trade', 0)))
    plans = {str(key): value for key, value in plans_by_id.items()}
    missing = set()

    def bar_of(ticker):
        bar = bars_by_ticker.get(ticker)
        if bar is None or bar['price_date'] != session:
            missing.add(ticker)
            return None
        return bar

    def plan_of(plan_id):
        p = plans[str(plan_id)]
        factor = plan_shadow._basis_factor(p['signal_date'], splits_by_ticker.get(p['ticker'], []))
        return {**p, **{key: Decimal(str(p[key])) / factor
                        for key in ('entry_low', 'entry_high', 'stop', 'target1', 'atr')}}

    def session_factor(ticker):
        return plan_shadow._basis_factor(session, splits_by_ticker.get(ticker, []))

    def fill(ticker, side, qty, price, plan_id, detail, *, at_open=False):
        factor = session_factor(ticker)
        executed_at = datetime.combine(session, time(9, 30) if at_open else time(16), NY)
        # Daily bars cannot reveal intraday order. Preserve shadow prices, but never
        # timestamp a fill-day sale before its buy (or tie on random trade UUIDs).
        prior = [f['executed_at'] for f in result['fills'] if f['ticker'] == ticker]
        if prior and executed_at <= max(prior):
            executed_at = max(prior) + timedelta(microseconds=1)
        result['fills'].append({'account_id': account['id'], 'ticker': ticker, 'side': side,
                                'quantity': qty / factor, 'price': price * factor, 'fee': fee,
                                'executed_at': executed_at,
                                'plan_id': plan_id, 'recorded_by': 'host',
                                'detail': {**detail, 'paper_fill_version': PAPER_FILL_VERSION}})

    def update(order, status, reason=None):
        result['order_updates'].append({'order_id': order['id'], 'status': status, 'status_reason': reason})

    def done(row, reason):
        p = plans[str(row['plan_id'])]
        item = {'ticker': row['ticker'], 'chain_signal_date': row.get('chain_signal_date', p['signal_date']),
                'reason': reason}
        if item not in result['chain_done']:
            result['chain_done'].append(item)

    active = [dict(h) for h in holdings]
    occupied = {h['ticker'] for h in holdings if Decimal(str(h['quantity'])) > 0}
    eligible = [o for o in pending_orders if o.get('status', 'pending') == 'pending'
                and o['placed_on'] < session and str(o.get('account_id', account['id'])) == str(account['id'])]
    sell_orders = {o['ticker']: o for o in eligible if o.get('action') == 'sell'}
    for order in eligible:
        if order.get('action', 'buy') != 'buy':
            continue
        if any(d['ticker'] == order['ticker'] and d['chain_signal_date'] == order['chain_signal_date']
               for d in result['chain_done']):
            update(order, 'rejected', 'chain_consumed')
            continue
        if order['ticker'] in occupied:
            # Defensive gate for raw/stale orders: never overwrite a ticker's frozen state.
            update(order, 'rejected', 'plan_not_buyable')
            continue
        p = plan_of(order['plan_id'])
        if session > p['valid_until']:
            update(order, 'expired', 'validity_expired')
            continue
        if session < p['valid_from']:
            continue
        bar = bar_of(order['ticker'])
        if bar is None:
            continue
        entry = entry_rules.simulate_entry(p, [bar])
        if entry['status'] == 'voided':
            update(order, 'voided', entry['void_reason'])
            done(order, 'entry_voided')
        elif entry['status'] == 'expired':
            update(order, 'expired', 'validity_expired')
        elif entry['status'] == 'filled':
            current_qty = Decimal(str(order['shares'])) * plan_shadow._basis_factor(
                order['placed_on'], splits_by_ticker.get(order['ticker'], []))
            fill_case = {'in_zone': 'in_zone', 'gap_up': 'gap_up_limit', 'gap_down': 'gap_down_stop_buy'}[entry['case']]
            fill(order['ticker'], 'buy', current_qty, entry['fill_price'], order['plan_id'],
                 {'fill_case': fill_case, 'order_id': order['id']}, at_open=entry['case'] == 'in_zone')
            update(order, 'filled')
            occupied.add(order['ticker'])
            active.append({'ticker': order['ticker'], 'quantity': current_qty, 'plan_id': order['plan_id'],
                           'chain_signal_date': order['chain_signal_date'],
                           'state': PositionState(entry['fill_price'], p['stop'], p['stop'], entry['fill_price'] - p['stop'])})

    for holding in active:
        ticker, plan_id = holding['ticker'], holding['plan_id']
        qty = Decimal(str(holding['quantity']))
        if qty <= 0:
            continue
        state = _state(holding['state'])
        bar = bar_of(ticker)
        if bar is None:
            result['states'][ticker] = state
            continue
        p = plan_of(plan_id)
        op = Decimal(str(bar['open']))
        sell_order = sell_orders.get(ticker)
        agent_sell = sell_order['portion'] if sell_order else holding.get('agent_sell')
        if agent_sell:
            sold = qty if agent_sell == 'all' else _half(qty, session_factor(ticker))
            if sold > 0:
                detail = {'exit_reason': 'agent_sell'}
                if sell_order:
                    detail['order_id'] = sell_order['id']
                fill(ticker, 'sell', sold, op, plan_id, detail, at_open=True)
                qty -= sold
                if sell_order:
                    update(sell_order, 'filled')
            elif sell_order:
                update(sell_order, 'rejected', 'cannot_halve')
        if qty > 0 and holding.get('exit_instruction'):
            fill(ticker, 'sell', qty, op, plan_id, {'exit_reason': 'exit_instruction'}, at_open=True)
            qty = Decimal(0)
        if qty > 0:
            previous_stop = state.stop
            state, legs = step_position(state, bar, target1=p['target1'], atr=p['atr'],
                                         trail_multiple=Decimal(str(p['trail_atr_multiple'])),
                                         max_hold=p.get('max_hold_sessions') or MAX_HOLD_DEFAULT)
            for leg in legs:
                reason = leg['reason']
                if reason == 'target1':
                    sold, exit_reason = _half(qty, session_factor(ticker)), 'target1_half'
                else:
                    sold = qty
                    exit_reason = ('gap_stop' if op <= previous_stop else 'stop') if reason in ('stop', 'trail') else reason
                if sold <= 0:
                    continue  # state already moved past T1 in the shadow; nothing to sell
                fill(ticker, 'sell', sold, leg['exit_price'], plan_id, {'exit_reason': exit_reason},
                     at_open=exit_reason in ('gap_stop', 'time_stop') or
                     (exit_reason == 'target1_half' and op >= p['target1']))
                qty -= sold
        if qty == 0:
            done(holding, 'exited')
        else:
            result['states'][ticker] = state
    result['bars_missing'] = sorted(missing)
    return result
