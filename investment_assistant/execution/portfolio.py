"""Account-scoped portfolio hard checks; all facts and cutoffs belong to the caller."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from decimal import Decimal

from investment_assistant.config import WorkbenchConfig
from investment_assistant.execution.accounts import default_params
from investment_assistant.execution.paper import strategies
from investment_assistant.execution.portfolio_types import PortfolioResult as PortfolioResult

ZERO = Decimal(0)


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _entry(plan: dict) -> bool:
    state = plan.get('state', plan.get('current_real_state', 'entry'))
    if isinstance(state, dict):
        state = state['state']
    return state == 'entry' and (plan.get('inputs') or {}).get('chain_mode') != 'holding'


def _cap_reason(sizing: dict, *, equity) -> str:
    # The single-order cap includes the risk budget; no extra public reason code.
    if equity <= 0:
        return 'insufficient_cash'
    return {'risk': 'single_cap', 'single': 'single_cap', 'total': 'total_cap',
            'cash': 'insufficient_cash', 'max_positions': 'max_positions'}[sizing['applied_cap']]


def check(*, session, plans, positions, pending, cash, equity, params, sectors) -> PortfolioResult:
    """Check entry heads, then greedily reserve their full account-sized quantities.

    Plans use ``inputs.chain_mode`` and optional timeline ``state`` (string or dict).
    Holdings are counted/valued from positions, never again from holding plan rows.
    Positions carry quantity and market_value (or close); pending buys carry frozen
    entry_high or reserved_cash, including fees, just like validate_orders.
    The caller has already selected effective heads and facts for ``session``.
    """
    params = {**default_params(WorkbenchConfig()), **params}
    cash, equity = _dec(cash), _dec(equity)
    fee = _dec(params['fee_per_trade'])
    sectors = {ticker.strip().upper(): group for ticker, group in sectors.items()}
    exposure, members = {}, defaultdict(set)

    def bucket(ticker):
        group = sectors.get(ticker)
        if group not in exposure:
            exposure[group] = {'held_value': ZERO, 'pending_value': ZERO, 'candidate_value': ZERO,
                               'share_of_equity': None}
        members[group].add(ticker)
        return exposure[group]

    held_values = defaultdict(lambda: ZERO)
    for position in positions:
        if _dec(position['quantity']) <= 0:
            continue
        ticker = position['ticker'].strip().upper()
        value = position.get('market_value')
        if value is None:
            value = _dec(position['quantity']) * _dec(position.get('close') or 0)
        held_values[ticker] += _dec(value)
        bucket(ticker)['held_value'] += _dec(value)

    items = []
    for plan in plans:
        if not _entry(plan):
            continue
        item = {**plan, 'ticker': plan['ticker'].strip().upper(), 'plan_id': plan.get('plan_id', plan.get('id'))}
        # Minimal plan facts may omit the entry band's lower edge.
        item.setdefault('entry_low', item.get('ref_price', item['entry_high']))
        items.append(item)
    items.sort(key=lambda item: (strategies._chain(item)[1], item['ticker']))
    by_id = {str(item['plan_id']): item for item in items}
    active = [row for row in pending if row.get('status', 'pending') == 'pending' and row.get('action', 'buy') == 'buy']
    reserved = defaultdict(lambda: ZERO)
    for row in active:
        ticker = row['ticker'].strip().upper()
        cash_reserved, value = strategies._reservation(row, by_id, fee)
        reserved[ticker] += cash_reserved
        bucket(ticker)['pending_value'] += value

    held_total = sum(held_values.values(), ZERO)
    counts = Counter(item['ticker'] for item in items)
    sizings, candidates = {}, {}
    for item in items:
        ticker, pid = item['ticker'], str(item['plan_id'])
        sizing = strategies.max_shares(item, equity=equity, params=params, cash=cash,
                                       positions_value_by_ticker=held_values, total_positions_value=held_total,
                                       pending_reserved=dict(reserved), positions_count=len(held_values),
                                       pending_count=len(active))
        sizings[pid] = sizing
        value = _dec(sizing['shares']) * _dec(item['entry_high'])
        # An already occupied ticker is not an additional candidate exposure.
        candidates[pid] = ZERO if ticker in held_values or ticker in reserved or counts[ticker] > 1 else value
        bucket(ticker)['candidate_value'] += candidates[pid]

    for values in exposure.values():
        total = values['held_value'] + values['pending_value'] + values['candidate_value']
        values['share_of_equity'] = total / equity if equity > 0 else None
    over_count = {group for group, tickers in members.items()
                  if group is not None and len(tickers) > params['max_per_sector']}
    conflicts = [{'group': group, 'plan_ids': [item['plan_id'] for item in items if sectors.get(item['ticker']) == group],
                  'rule': 'max_per_sector'} for group in sorted(over_count)]
    conflicts = [conflict for conflict in conflicts if conflict['plan_ids']]
    verdicts, order = [], []
    running_reserved = dict(reserved)
    pending_count = len(active)
    for item in items:
        ticker, pid = item['ticker'], str(item['plan_id'])
        group, reasons = sectors.get(ticker), []
        share = exposure[group]['share_of_equity']
        if group is not None and share is not None and share > _dec(params['sector_cap']):
            reasons.append('sector_cap')
        if group in over_count:
            reasons.append('max_per_sector')
        if ticker in held_values or ticker in reserved or counts[ticker] > 1:
            reasons.append('held_ticker')
        elif sizings[pid]['shares'] <= 0:
            reasons.append(_cap_reason(sizings[pid], equity=equity))
        verdict, rank = 'blocked' if reasons else 'ok', None
        if not reasons:
            cost = candidates[pid] + fee
            if cost > cash - sum(running_reserved.values(), ZERO):
                verdict, reasons = 'deferred', ['deferred_by_cash']
            else:
                available = strategies.max_shares(item, equity=equity, params=params, cash=cash,
                                                  positions_value_by_ticker=held_values,
                                                  total_positions_value=held_total, pending_reserved=running_reserved,
                                                  positions_count=len(held_values), pending_count=pending_count)
                if available['shares'] < sizings[pid]['shares']:
                    verdict, reasons = 'blocked', [_cap_reason(available, equity=equity)]
                else:
                    order.append(item['plan_id'])
                    rank = len(order)
                    running_reserved[ticker] = running_reserved.get(ticker, ZERO) + cost
                    pending_count += 1
        if group is None:
            reasons.append('sector_unknown')
        verdicts.append({'plan_id': item['plan_id'], 'ticker': ticker, 'group': group,
                         'verdict': verdict, 'reasons': reasons, 'order': rank})
    return PortfolioResult(exposure, verdicts, conflicts, order, params)


def to_fact(result: PortfolioResult) -> dict:
    """Detached fact payload, preserving exact numbers and a readable unclassified key."""
    fact = asdict(result)
    fact['exposure_by_sector'] = {'未归类' if group is None else group: values
                                  for group, values in fact['exposure_by_sector'].items()}
    return fact


def verdict_for(result: PortfolioResult, plan_id) -> dict | None:
    return next((verdict for verdict in result.verdicts if str(verdict['plan_id']) == str(plan_id)), None)
