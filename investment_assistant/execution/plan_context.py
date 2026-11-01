"""execution plan_context use cases; model calls belong to analysis."""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from investment_assistant.core import schema as core_schema
from investment_assistant.core.constants import NY, REAL_ACCOUNT_ID
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import repo as execution_repo

from investment_assistant.execution import positions, plan_levels as plans

from investment_assistant.core.constants import FILING_LOOKBACK_DAYS


def _book(conn, ticker: str, *, account_id=REAL_ACCOUNT_ID) -> dict[str, Any]:
    return positions.build(execution_repo.list_trades(conn, ticker, account_id=account_id), splits=data_repo.split_events(conn, ticker))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=NY)


def _filing_events(conn, ticker: str, session: date, cutoff_at: datetime,
                   since: date | None = None) -> tuple[list, list]:
    """(rule inputs, filings rows). SEC 8-K listings not in the filings table still count as new filings.
    ``since`` (default: FILING_LOOKBACK_DAYS before the session) is the first filing date read."""
    since = session - timedelta(days=FILING_LOOKBACK_DAYS) if since is None else since
    rows = data_repo.read_filings(conn, ticker, since=since, cutoff_at=cutoff_at)
    events = [{**r, 'id': f"filing:{r['id']}"} for r in rows]
    known = {r['accession_number'].replace('-', '') for r in rows}
    for item in data_repo.read_news(conn, ticker, cutoff_at=cutoff_at, since=_midnight(since)):
        if item['source'] != 'sec_8k' or any(acc in item['url'] for acc in known):
            continue
        seen = item['published_at'] or item['fetched_at']
        events.append({'id': f"news:{item['id']}", 'form': '8-K', 'items': None,
                       'filing_date': seen.astimezone(NY).date(), 'accepted_at': item['published_at']})
    return events, rows


def exposure(conn, session: date, *, cutoff_at: datetime, ticker: str) -> dict[str, Decimal]:
    """Market value of the ticker's open position and of all open positions, at each latest close."""
    from decimal import Decimal

    ticker_value = Decimal('0')
    total_value = Decimal('0')
    for held in sorted({t['ticker'] for t in execution_repo.list_trades(conn) if t['voided_at'] is None}):
        pos = _book(conn, held)['position']
        if pos is None:
            continue
        latest = data_repo.read_bars(conn, held, session, cutoff_at=cutoff_at, limit=1)
        value = Decimal(str(pos['quantity'])) * Decimal(str(latest[-1]['close'])) if latest else Decimal('0')
        total_value += value
        if held == ticker.upper():
            ticker_value = value
    return {'ticker_value': ticker_value, 'total_value': total_value}


def plan_context(conn, ticker: str, session: date, *, now: datetime,
                 screen_ref: dict[str, Any] | None = None) -> dict[str, Any]:
    """Market context frozen into a plan: tier and source at the signal date, regime, sector,
    earnings, and the screen pick that put the ticker in the pool."""
    entry = data_repo.pool_entry_at(conn, ticker, session, cutoff_at=now)
    tier = entry['tier'] if entry and entry['tier'] in ('core', 'extended') else 'unknown'
    regime_row = data_repo.regime_for(conn, session, cutoff_at=now)
    sector = None
    if entry and entry.get('sector_etf'):
        snap_sector = (regime_row or {}).get('sectors', {}).get(entry['sector_etf'])
        sector = ({'etf': entry['sector_etf'], 'rs20': snap_sector['rs20'], 'rs60': snap_sector['rs60'],
                   'state': snap_sector['state']} if snap_sector is not None
                  else {'etf': entry['sector_etf'], 'rs20': None, 'rs60': None, 'state': None})
    earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=now)
    next_earnings = (earnings['earnings_date'] if earnings and earnings['status'] == 'known'
                     and earnings['earnings_date'] and earnings['earnings_date'] >= session else None)
    return {'context_version': plans.CONTEXT_VERSION, 'pool_tier': tier,
            'source': entry.get('source') if entry else None,
            'screen': screen_ref,
            'regime': ({'id': str(regime_row['id']), 'label': regime_row['label']}
                       if regime_row is not None else None),
            'sector': sector, 'next_earnings': next_earnings}


def sizing_equity(conn, session: date, *, now: datetime) -> dict | None:
    """A real opened ledger takes priority; a not-yet-valued ledger keeps the existing snapshot fallback."""
    if core_schema.accounts_ready(conn):
        account_ = execution_repo.account_by_id(conn, REAL_ACCOUNT_ID)
        rows = execution_repo.equity_days(conn, REAL_ACCOUNT_ID, to_session=session, cutoff_at=now)
        if account_ and account_['opened_on'] and account_['opened_on'] <= session and rows:
            row = rows[-1]
            return {'id': row['id'], 'as_of': row['session'], 'equity': row['equity'], 'equity_source': 'ledger'}
    snapshot = execution_repo.snapshot_at(conn, session, cutoff_at=now)
    return {**snapshot, 'equity_source': 'snapshot'} if snapshot else None


def mark_sizing_source(plan: dict, source: dict | None) -> None:
    if 'sizing' in plan:
        plan['sizing']['equity_source'] = source.get('equity_source', 'snapshot') if source else 'snapshot'


def _generate_rule_plan(conn, ticker: str, session: date, ticker_bars: list[dict[str, Any]], summary: dict[str, Any],
                        *, now: datetime, active_chain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Build and store the rule baseline (`plan-2`, v2.9 §5.4) for a fresh buy unit; record skip/conflict in the summary.

    ``active_chain`` (``{'ticker', 'signal_date'}``) marks a unit that joined an active chain: the plan row keeps
    ``inputs.active_chain``. Returns None when the plan was skipped, else ``{'status': inserted|existing|conflict,
    'id': plan_id, 'plan_status': ok|invalid}`` for the stage accounting (v2.4 §4); ``plan_status`` is the stored
    plan row's own status. The summary is written exactly as before."""
    from investment_assistant.config import load_config

    entry = data_repo.pool_entry_at(conn, ticker, session, cutoff_at=now)
    screen_ref = (data_repo.latest_screen_pick(conn, ticker, session)
                  if entry is not None and entry.get('source') == 'screen' else None)
    equity_source = sizing_equity(conn, session, now=now)
    plan = plans.build_rule_plan(
        ticker=ticker, signal_date=session, bars=ticker_bars,
        earnings_row=data_repo.latest_earnings(conn, ticker, cutoff_at=now),
        snapshot=equity_source,
        exposure=exposure(conn, session, cutoff_at=now, ticker=ticker),
        params=load_config().workbench,
        context=plan_context(conn, ticker, session, now=now, screen_ref=screen_ref),
        version=plans.PLAN_VERSION, active_chain=active_chain)
    mark_sizing_source(plan, equity_source)
    if 'skip' in plan:
        summary['plans'][ticker] = f"skip: {plan['skip']}"
        return None
    out = execution_repo.store_plan(conn, plan)
    summary['plans'][ticker] = out['status']
    if out['status'] == 'conflict':
        summary['conflicts'].append({'ticker': ticker, 'rule_id': 'rule_plan', 'id': str(out['record']['id'])})
    return {'status': out['status'], 'id': out['record']['id'], 'plan_status': out['record']['status']}
