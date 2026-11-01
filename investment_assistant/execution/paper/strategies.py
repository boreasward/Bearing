"""Paper-account candidates, sizing and order gates; all records supplied by the caller."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_FLOOR

from investment_assistant.config import WorkbenchConfig
from investment_assistant.core.texts import REJECT_TEXT as REJECT_TEXT
from investment_assistant.core.versions import FOLLOW_PLANS_VERSION as FOLLOW_PLANS_VERSION
from investment_assistant.execution import plan_levels as plans
from investment_assistant.execution import portfolio_check
from investment_assistant.execution.citations import Rejected

REJECT_CODES = ('schema_invalid', 'plan_not_buyable', 'chain_consumed', 'duplicate_order',
                'exceeds_max_shares', 'max_positions', 'insufficient_cash', 'no_position',
                'cannot_halve', 'order_not_pending')
ZERO = Decimal(0)


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _chain(item: dict) -> tuple:
    day = item.get('chain_signal_date', item.get('signal_date'))
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return item['ticker'].strip().upper(), day


def buyable(chain_items: list[dict], *, next_session: date) -> list[dict]:
    """Read execution_chains.ticker_chain_items' real {chain, state, facts, buys} shape.

    Preferred enrichment: each chain.versions entry has `plan` = its full trade_plans
    row. Alternatively item.plans_by_id (or chain.plans_by_id) maps plan_id to the row.
    Native head_plan suffices when the latest version is an entry plan. A holding head
    requires older plans: missing rows raise ValueError, never guessed entry levels.
    All versions/plans/reviews must already be cut at the caller's decision cutoff.
    """
    result = []
    for item in chain_items:
        chain = item['chain']
        real_state = item['state']['state'] if isinstance(item['state'], dict) else item['state']
        if real_state in ('invalidated', 'expired', 'superseded') or chain.get('legacy'):
            continue
        head = chain.get('head_plan')
        if head is None or head.get('plan_version') != 'agent-3':
            continue
        supplied = item.get('plans_by_id', chain.get('plans_by_id', {}))
        by_id = {str(k): v for k, v in supplied.items()}
        by_id[str(head['id'])] = head
        selected = None
        for version in sorted(chain['versions'], key=lambda v: v['version_no'], reverse=True):
            plan = version.get('plan') or by_id.get(str(version['plan_id']))
            if plan is None:
                raise ValueError(f"missing plan for version {version['plan_id']}")
            if (plan.get('inputs') or {}).get('chain_mode') != 'holding':
                selected = version, plan
                break
        if selected is None:
            continue
        version, plan = selected
        if plan.get('plan_version') != 'agent-3' or not plan['valid_from'] <= next_session <= plan['valid_until']:
            continue
        if plan.get('entry_type') == 'next_open' and next_session != plan['valid_from']:
            continue
        reviews = chain.get('reviews') or []
        latest_review = max(reversed(reviews), key=lambda r: (str(r.get('session', '')), str(r.get('created_at', ''))), default=None)
        changes = version.get('changes') or version.get('diff') or []
        change = '；'.join(f"{v['field']}：{v.get('before')} → {v.get('after')}" if isinstance(v, dict) and 'field' in v
                          else str(v) for v in changes)
        result.append({**plan, 'plan_id': plan['id'], 'ticker': chain['ticker'],
                       'chain_signal_date': chain['signal_date'], 'version_no': version['version_no'],
                       'latest_review': latest_review, 'change': change, 'current_real_state': real_state})
    return result


def account_filter(items: list[dict], *, held_chains: set, done: set, tried_plan_ids: set,
                   pending_chains: set) -> list[dict]:
    """Keep blocked plans visible. One open position per ticker preserves its frozen plan.

    Chain sets contain (ticker, chain_signal_date); passed records are account-scoped.
    """
    def normalize(pairs):
        return {_chain({'ticker': t, 'chain_signal_date': d}) for t, d in pairs}
    held, finished, pending = normalize(held_chains), normalize(done), normalize(pending_chains)
    tried = {str(p) for p in tried_plan_ids}
    held_tickers, pending_tickers = {t for t, _ in held}, {t for t, _ in pending}
    result = []
    for item in items:
        chain = _chain(item)
        reason = None
        if chain in finished:
            reason = 'chain_consumed'
        elif str(item['plan_id']) in tried:
            reason = 'version_tried'
        elif chain in held:
            reason = 'held_chain'
        elif chain[0] in held_tickers:
            reason = 'held_ticker'
        elif chain in pending:
            reason = 'pending_chain'
        elif chain[0] in pending_tickers:
            reason = 'pending_ticker'
        result.append({**item, 'blocked_reason': reason})
    return result


def max_shares(item: dict, *, equity, params: dict, cash, positions_value_by_ticker: dict,
               total_positions_value, pending_reserved, positions_count: int, pending_count: int) -> dict:
    """plans.size's unchanged three caps, then position count and available cash.

    pending_reserved is total reserved cash (including fees), or a ticker->amount map
    when single-ticker pending exposure must also be subtracted. Levels and quantities
    supplied here must share the decision day's basis.
    """
    if positions_count + pending_count >= params.get('max_positions', 8):
        return {'shares': 0, 'applied_cap': 'max_positions', 'status': 'max_positions'}
    if isinstance(pending_reserved, dict):
        reserve = sum((_dec(v) for v in pending_reserved.values()), ZERO)
        ticker_reserve = _dec(pending_reserved.get(item['ticker'], 0))
    else:
        reserve, ticker_reserve = _dec(pending_reserved), ZERO
    ref_price = _dec(item.get('ref_price', (_dec(item['entry_low']) + _dec(item['entry_high'])) / 2))
    stop, upper = _dec(item['stop']), _dec(item['entry_high'])
    if ref_price <= stop or upper <= 0 or _dec(equity) <= 0:
        return {'shares': 0, 'applied_cap': 'risk', 'status': 'no_room'}
    cfg = WorkbenchConfig(**{k: params[k] for k in ('risk_pct', 'single_cap', 'total_cap')})
    sized, _ = plans.size(snapshot={'id': None, 'as_of': None, 'equity': _dec(equity)}, ref_price=ref_price,
                          stop=stop, entry_high=upper, ticker_value=_dec(positions_value_by_ticker.get(item['ticker'], 0))
                          + ticker_reserve, total_value=_dec(total_positions_value) + reserve, params=cfg)
    cash_cap = int(((_dec(cash) - reserve - _dec(params.get('fee_per_trade', 0))) / upper)
                   .to_integral_value(rounding=ROUND_FLOOR))
    shares, cap, status = sized['shares'], sized['applied_cap'], sized['status']
    if cash_cap < shares:
        shares, cap, status = cash_cap, 'cash', 'capped'
    return {'shares': max(0, shares), 'applied_cap': cap, 'status': status if shares > 0 else 'no_room'}


def follow_plans_orders(items: list[dict], *, pending_orders: list[dict], session: date) -> dict:
    """Mechanical intents at caller-computed caps; caller reserves capacity between placements.

    Terminal rows, if included, are used only to prevent retries (host-rejected ones never consume a version). Pending old-version
    buys are cancelled before replacing the version, freeing the caller's reservation.
    """
    result = {'place': [], 'cancel': [], 'skipped': []}
    by_chain = {_chain(i): i for i in items}
    tried = {str(o['plan_id']) for o in pending_orders if o.get('action', 'buy') == 'buy'
             and o.get('status') != 'rejected'}
    consumed = {_chain(o) for o in pending_orders if o.get('action', 'buy') == 'buy'
                and o.get('status') in ('voided', 'filled')}
    active = [o for o in pending_orders if o.get('status', 'pending') == 'pending' and o.get('action', 'buy') == 'buy']
    kept, replaced = set(), set()
    for order in active:
        key = _chain(order)
        latest = by_chain.get(key)
        if latest is None:
            result['cancel'].append({'order_id': order['id'], 'reason': 'chain_ended'})
        elif str(latest['plan_id']) != str(order['plan_id']):
            result['cancel'].append({'order_id': order['id'], 'reason': 'superseded_by_revision'})
            replaced.add(key)
        else:
            kept.add(key)
    occupied_tickers = {ticker for ticker, _ in kept}
    for item in items:
        key, pid = _chain(item), str(item['plan_id'])
        reason = item.get('blocked_reason')
        if reason == 'pending_chain' and key in replaced:
            reason = None
        if key in kept or key in consumed or pid in tried or reason or key[0] in occupied_tickers:
            continue
        shares = item.get('max_shares', 0)
        if shares <= 0:
            result['skipped'].append({'plan_id': item['plan_id'],
                                      'reason': 'max_positions' if item.get('sizing_status') == 'max_positions' else 'no_room'})
            continue
        result['place'].append({'action': 'buy', 'plan_id': item['plan_id'], 'ticker': item['ticker'],
                                'chain_signal_date': item['chain_signal_date'], 'shares': shares,
                                'placed_on': session, 'placed_by': 'host', 'reason': '照计划执行'})
        occupied_tickers.add(key[0])
    return result


def _reservation(order: dict, items_by_id: dict, fee: Decimal) -> tuple[Decimal, Decimal]:
    """(cash, exposure). Pending orders need their own frozen entry_high or reserved_cash."""
    if order.get('reserved_cash') is not None:
        reserved = _dec(order['reserved_cash'])
        return reserved, max(ZERO, reserved - fee)
    plan = order.get('plan') or items_by_id.get(str(order['plan_id'])) or {}
    upper = order.get('entry_high', plan.get('entry_high'))
    if upper is None:
        raise ValueError(f"pending order {order['id']} missing frozen entry_high or reserved_cash")
    exposure = _dec(order['shares']) * _dec(upper)
    return exposure + fee, exposure


def validate_orders(output: dict, *, items: list[dict], positions: list[dict], pending_orders: list[dict],
                    cash, params: dict) -> dict:
    """Never resize invalid orders. Structural errors reject all; other gates are per-order.

    Inputs are scoped to one account. Pending buys must carry `entry_high` from their
    frozen plan in shares' basis, or `reserved_cash` including fees; a matching item
    also supplies entry_high. Positions carry quantity and market_value (or close).
    Valid cancellations release reservations before any buy, regardless of JSON order.
    Sales never fund today's buys: they execute tomorrow at an unknown opening price.
    """
    held = {p['ticker'].strip().upper(): p for p in positions if _dec(p['quantity']) > 0}
    try:
        portfolio_check.validate_schema(output, expected_positions=held)
    except Rejected as exc:
        orders = output.get('orders', []) if isinstance(output, dict) else []
        return {'accepted': [], 'rejected': [{'order': o, 'code': 'schema_invalid'} for o in orders]
                if isinstance(orders, list) else [], 'schema_error': exc.reason}
    orders = output['orders']
    candidates = {str(i['plan_id']): i for i in items}
    active = {str(o['id']): o for o in pending_orders
              if o.get('status', 'pending') == 'pending' and o.get('action', 'buy') == 'buy'}
    prior_versions = {str(o['plan_id']) for o in pending_orders if o.get('action', 'buy') == 'buy'
                      and o.get('status') != 'rejected'}
    consumed = {_chain(o) for o in pending_orders if o.get('action', 'buy') == 'buy'
                and o.get('status') in ('voided', 'filled')}
    reasons = {}
    seen_cancels = set()
    for index, order in enumerate(orders):
        if order['action'] != 'cancel':
            continue
        oid = order['order_id']
        if oid in seen_cancels:
            reasons[index] = 'duplicate_order'
        elif oid not in active:
            reasons[index] = 'order_not_pending'
        else:
            del active[oid]
        seen_cancels.add(oid)
    fee = _dec(params.get('fee_per_trade', 0))
    reserved = [_reservation(order, candidates, fee) for order in active.values()]
    remaining_cash = _dec(cash) - sum((r[0] for r in reserved), ZERO)
    held_value = sum((_dec(p.get('market_value', _dec(p['quantity']) * _dec(p.get('close') or 0)))
                      for p in held.values()), ZERO)
    equity = _dec(cash) + held_value
    total_room = equity * _dec(params.get('total_cap', 1)) - held_value - sum((r[1] for r in reserved), ZERO)
    occupied = {o['ticker'].strip().upper() for o in active.values()}
    pending_chains = {_chain(o) for o in active.values()}
    position_count = len(held) + len(active)
    seen_buys = set()
    seen_sells = {o['ticker'].strip().upper() for o in pending_orders
                  if o.get('action') == 'sell' and o.get('status', 'pending') == 'pending'}
    for index, order in enumerate(orders):
        action, code = order['action'], None
        if action == 'cancel':
            continue
        if action == 'sell':
            ticker = order['ticker'].strip().upper()
            if ticker in seen_sells:
                code = 'duplicate_order'
            elif ticker not in held:
                code = 'no_position'
            elif order['portion'] == 'half' and _dec(held[ticker]['quantity']) < 2:
                code = 'cannot_halve'
            seen_sells.add(ticker)
        else:
            item = candidates.get(order['plan_id'])
            if item is None:
                code = 'plan_not_buyable'
            else:
                key, reason, shares = _chain(item), item.get('blocked_reason'), order['shares']
                if key in consumed or reason in ('chain_consumed', 'version_tried'):
                    code = 'chain_consumed'
                elif key[0] in held or reason in ('held_chain', 'held_ticker'):
                    code = 'plan_not_buyable'
                elif key in seen_buys or key in pending_chains or key[0] in occupied:
                    code = 'duplicate_order'
                elif order['plan_id'] in prior_versions:
                    code = 'chain_consumed'
                elif reason and reason not in ('pending_chain', 'pending_ticker'):
                    code = 'plan_not_buyable'
                elif type(shares) is not int or shares <= 0 or shares > item.get('max_shares', 0):
                    code = 'exceeds_max_shares'
                elif position_count >= params.get('max_positions', 8):
                    code = 'max_positions'
                else:
                    exposure = _dec(shares) * _dec(item['entry_high'])
                    if exposure + fee > remaining_cash:
                        code = 'insufficient_cash'
                    elif exposure > total_room:
                        code = 'exceeds_max_shares'
                    else:
                        remaining_cash -= exposure + fee
                        total_room -= exposure
                        position_count += 1
                        occupied.add(key[0])
                        pending_chains.add(key)
                seen_buys.add(key)
        if code:
            reasons[index] = code
    accepted = []
    for index, order in enumerate(orders):
        if index in reasons:
            continue
        canonical = dict(order)
        if canonical['action'] == 'sell':
            # Persist the same symbol we validated; settlement matches exact keys.
            canonical['ticker'] = canonical['ticker'].strip().upper()
        accepted.append(canonical)
    return {'accepted': accepted,
            'rejected': [{'order': o, 'code': reasons[i]} for i, o in enumerate(orders) if i in reasons],
            'schema_error': None}
