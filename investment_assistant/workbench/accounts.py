"""Account validation and ledger math (`account-1`). Pure, account-scoped inputs only."""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from investment_assistant.config import WorkbenchConfig
from investment_assistant.data.calendar import add_trading_days, is_trading_day
from investment_assistant.workbench import positions

ACCOUNT_VERSION = 'account-1'
REAL_ACCOUNT_ID = UUID('00000000-0000-0000-0000-000000000001')
DEFAULT_PAPER_CASH = Decimal('100000')
STRATEGIES = ('manual', 'agent_managed', 'follow_plans')
STRATEGY_TEXT = {'manual': '手动', 'agent_managed': 'Agent 自管', 'follow_plans': '照计划执行'}
ORDER_STATUS_TEXT = {'pending': '待成交', 'filled': '已成交', 'voided': '已作废', 'expired': '已过期',
                     'cancelled': '已撤单', 'rejected': '未通过校验'}
RECONCILE_TEXT = {'matched': '对账一致', 'mismatch': '待核对', 'no_ledger': '暂无账本权益'}
NY = ZoneInfo('America/New_York')
ZERO = Decimal(0)


def default_params(cfg: WorkbenchConfig) -> dict:
    return {'risk_pct': cfg.risk_pct, 'single_cap': cfg.single_cap, 'total_cap': cfg.total_cap,
            'fee_per_trade': 0, 'max_positions': 8, 'reconcile_tolerance_pct': .01,
            'reconcile_tolerance_abs': 100}


def _number(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{field} must be a finite number')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f'{field} must be a finite number') from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f'{field} must be finite and {"positive" if positive else "non-negative"}')
    return number


def _day(value: Any, field: str = 'opened_on') -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f'{field} must be an ISO date')


def validate_params(payload: dict) -> dict:
    """Validate a partial parameter update; merging and effective dates belong to the caller."""
    if not isinstance(payload, dict):
        raise ValueError('params must be an object')
    allowed = set(default_params(WorkbenchConfig()))
    if payload.keys() - allowed:
        raise ValueError('unknown account parameter')
    out = {}
    for key, value in payload.items():
        if key == 'max_positions':
            if type(value) is not int or not 1 <= value <= 30:
                raise ValueError('max_positions must be an integer in 1..30')
            out[key] = value
            continue
        positive = key in ('risk_pct', 'single_cap', 'total_cap')
        number = _number(value, key, positive=positive)
        upper = Decimal('.05') if key == 'risk_pct' else Decimal(1)
        if positive and number > upper:
            raise ValueError(f'{key} exceeds {upper}')
        out[key] = number
    return out


def validate_opening(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('opening must be an object')
    opening = _number(payload.get('opening_cash'), 'opening_cash', positive=True)
    opened_on = _day(payload.get('opened_on'))
    rows = payload.get('opening_positions', [])
    if not isinstance(rows, list):
        raise ValueError('opening_positions must be a list')
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('ticker'), str):
            raise ValueError('opening position must contain a ticker')
        ticker = row['ticker'].strip().upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-^=]{0,19}', ticker) or ticker in seen:
            raise ValueError('opening ticker must be valid and unique')
        seen.add(ticker)
        result.append({'ticker': ticker, 'quantity': _number(row.get('quantity'), 'quantity', positive=True),
                       'cost_per_share': _number(row.get('cost_per_share'), 'cost_per_share', positive=True)})
    return {'opening_cash': opening, 'opened_on': opened_on, 'opening_positions': result}


def validate_new_account(payload: dict, *, defaults: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('account must be an object')
    try:
        request_id = UUID(str(payload.get('request_id')))
    except (ValueError, TypeError):
        raise ValueError('request_id must be a UUID') from None
    name = payload.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('name must be nonempty')
    if payload.get('kind', 'paper') != 'paper' or payload.get('strategy') not in STRATEGIES[1:]:
        raise ValueError('new accounts must be paper with agent_managed or follow_plans strategy')
    params = validate_params(defaults)
    params.update(validate_params(payload.get('params', {})))
    return {'request_id': request_id, 'name': name.strip(), 'kind': 'paper', 'strategy': payload['strategy'],
            'opening_cash': _number(payload.get('opening_cash', DEFAULT_PAPER_CASH), 'opening_cash', positive=True),
            'opened_on': _day(payload.get('opened_on')), 'params': params}


def _cutoff_day(cutoff: date | datetime) -> date:
    return cutoff.astimezone(NY).date() if isinstance(cutoff, datetime) else cutoff


def _live_trades(trades: list[dict], cutoff: date | datetime) -> list[dict]:
    return [t for t in trades if t.get('voided_at') is None and
            (t['executed_at'] <= cutoff if isinstance(cutoff, datetime)
             else _cutoff_day(t['executed_at']) <= cutoff)]


def cash(account: dict, trades: list[dict], flows: list[dict], *, cutoff: date | datetime) -> Decimal:
    if account.get('opening_cash') is None:
        raise ValueError('no_opening')
    total = Decimal(str(account['opening_cash']))
    total += sum((Decimal(str(f['amount'])) for f in flows if f.get('voided_at') is None
                  and f['flow_date'] <= _cutoff_day(cutoff)), ZERO)
    for trade in _live_trades(trades, cutoff):
        cost = Decimal(str(trade['quantity'])) * Decimal(str(trade['price']))
        fee = Decimal(str(trade.get('fee') or 0))
        if trade['side'] == 'buy':
            if not (trade.get('detail') or {}).get('opening_position'):
                total -= cost + fee
        else:
            total += cost - fee
    return total


def book(trades: list[dict], *, splits_by_ticker: dict) -> dict[str, dict]:
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade['ticker']].append(trade)
    return {ticker: positions.build(rows, splits=splits_by_ticker.get(ticker, []))
            for ticker, rows in grouped.items()}


def equity_day(account: dict, *, session: date, trades: list[dict], flows: list[dict],
               closes: dict[str, tuple[date, Decimal]], splits_by_ticker: dict,
               previous: dict | None, run_id: Any) -> dict:
    cutoff = datetime.combine(session, time.max, NY)
    live = _live_trades(trades, cutoff)
    books = book(live, splits_by_ticker=splits_by_ticker)
    cash_value = cash(account, live, flows, cutoff=cutoff)
    live_flows = [f for f in flows if f.get('voided_at') is None and f['flow_date'] <= session]
    net = sum((Decimal(str(f['amount'])) for f in live_flows), ZERO)
    value = realized = unrealized = ZERO
    detail = {'positions': {}, 'stale': {}, 'bars_missing': [],
              'cash_flows': [{'flow_date': f['flow_date'], 'amount': f['amount']} for f in live_flows]}
    for ticker, ticker_book in sorted(books.items()):
        realized += sum((lot['realized_pnl'] for lot in ticker_book['closed']), ZERO)
        position = ticker_book['position']
        if position is None:
            continue
        close = closes.get(ticker)
        if close is None or close[0] > session:
            close = None
            detail['bars_missing'].append(ticker)
        elif close[0] < session:
            # bars_missing means today's close is unavailable, even with a fallback.
            detail['bars_missing'].append(ticker)
            detail['stale'][ticker] = close[0]
        price = Decimal(str(close[1])) if close is not None else None
        market_value = position['quantity'] * price if price is not None else ZERO
        pnl = market_value - position['quantity'] * position['avg_cost']
        value += market_value
        unrealized += pnl
        detail['positions'][ticker] = {**position, 'close': price, 'close_session': close[0] if close else None,
                                       'market_value': market_value, 'unrealized_pnl': pnl}
    equity = cash_value + value
    return {'account_id': account['id'], 'session': session, 'cash': cash_value, 'positions_value': value,
            'equity': equity, 'realized_pnl_cum': realized, 'unrealized_pnl': unrealized, 'net_flows_cum': net,
            'day_pnl': None if previous is None else equity - Decimal(str(previous['equity']))
                       - (net - Decimal(str(previous['net_flows_cum']))),
            'positions_count': len(detail['positions']), 'detail': detail, 'account_version': ACCOUNT_VERSION,
            'run_id': run_id}


def _latest_rows(rows: list[dict]) -> list[dict]:
    """A correction replaces a day's earlier row in every read-side calculation."""
    latest = {}
    for row in sorted(rows, key=lambda r: (r['session'], str(r.get('computed_at', '')), str(r.get('id', '')))):
        latest[row['session']] = row
    return [latest[day] for day in sorted(latest)]


def opening_session(account: dict) -> date | None:
    """First session an account can hold value: opened_on itself, or the next trading day."""
    opened = account.get('opened_on')
    return add_trading_days(opened - timedelta(days=1), 1) if opened else None


def inception_return(account: dict, rows: list[dict]) -> float | None:
    """Modified Dietz from the opening session's equity row (D25 opening positions are part of the base).

    base = first equity net of cumulative flows, so opening positions count as starting capital without
    inflating the return. None when the opening day has no complete valuation row.
    """
    if account.get('opening_cash') is None or not rows or account.get('opened_on') is None:
        return None
    rows = _latest_rows(rows)
    first = next((row for row in rows if row['session'] == opening_session(account)), None)
    if first is None:
        return None
    detail = first.get('detail') or {}
    if detail.get('bars_missing') or detail.get('stale'):
        return None
    last = rows[-1]
    base = Decimal(str(first['equity'])) - Decimal(str(first['net_flows_cum']))
    if base <= 0:
        return None
    days = (last['session'] - first['session']).days
    flow_records = (last.get('detail') or {}).get('cash_flows')
    if flow_records is None:
        flow_records, prior = [], ZERO
        for row in rows:
            cumulative = Decimal(str(row['net_flows_cum']))
            flow_records.append({'flow_date': row['session'], 'amount': cumulative - prior})
            prior = cumulative
    weighted = ZERO
    for flow in flow_records:
        # A flow before the first valued session (weekend opening) is part of that session's base weight.
        day = max(_day(flow['flow_date'], 'flow_date'), first['session'])
        if days > 0:
            weighted += Decimal(str(flow['amount'])) * Decimal((last['session'] - day).days) / days
    denominator = base + weighted
    if denominator <= 0:
        return None
    return float((Decimal(str(last['equity'])) - base - Decimal(str(last['net_flows_cum']))) / denominator)


def max_drawdown(rows: list[dict]) -> dict | None:
    rows = _latest_rows(rows)
    if not rows:
        return None
    peak = rows[0]
    result = {'pct': 0.0, 'peak_session': peak['session'], 'trough_session': peak['session']}
    for row in rows:
        if row['equity'] > peak['equity']:
            peak = row
        if peak['equity'] <= 0:
            continue
        pct = float((Decimal(str(peak['equity'])) - Decimal(str(row['equity']))) / Decimal(str(peak['equity'])))
        if pct > result['pct']:
            result = {'pct': pct, 'peak_session': peak['session'], 'trough_session': row['session']}
    return result


def month_window(rows: list[dict], month: date) -> tuple[dict | None, dict | None]:
    def last_session(day: date) -> date:
        while not is_trading_day(day):
            day -= timedelta(days=1)
        return day
    start_day = last_session(month.replace(day=1) - timedelta(days=1))
    end_day = last_session(month.replace(day=calendar.monthrange(month.year, month.month)[1]))
    latest = {row['session']: row for row in _latest_rows(rows)}
    return latest.get(start_day), latest.get(end_day)


def reconcile(snapshots: list[dict], rows: list[dict], params: dict) -> list[dict]:
    superseded = {s.get('supersedes_id') for s in snapshots if s.get('supersedes_id')}
    rows = [row for row in _latest_rows(rows) if is_trading_day(row['session'])]
    out = []
    for snap in snapshots:
        if snap['id'] in superseded or snap.get('voided_at') is not None:
            continue
        ledger = next((r for r in reversed(rows) if r['session'] <= snap['as_of']), None)
        snapshot_equity = Decimal(str(snap['equity']))
        ledger_equity = Decimal(str(ledger['equity'])) if ledger else None
        diff = snapshot_equity - ledger_equity if ledger else None
        pct = diff / ledger_equity if ledger_equity else (ZERO if diff == 0 else None)
        status = 'no_ledger'
        if ledger:
            mismatch = abs(diff) > Decimal(str(params.get('reconcile_tolerance_abs', 100)))
            mismatch |= pct is None or abs(pct) > Decimal(str(params.get('reconcile_tolerance_pct', '.01')))
            status = 'mismatch' if mismatch else 'matched'
        out.append({'snapshot_id': snap['id'], 'as_of': snap['as_of'],
                    'ledger_session': ledger['session'] if ledger else None, 'snapshot_equity': snapshot_equity,
                    'ledger_equity': ledger_equity, 'diff': diff, 'pct': pct, 'status': status})
    return out
