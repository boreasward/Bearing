"""Read views of the v2.9 executable plans (spec §9.1, §10, D34, D35): the 「今日」 plan table, the 「标的」 plan history, the
day's holding-status rows and the plans a fill may be linked to.

Everything here is read-only and calls no outside source. The functions take a connection (the caller holds a read-only
transaction, ``service.read_only_tx``) and the cutoff ``now``; the ``*_view`` wrappers open that transaction for the
routes. A database without migration 029 answers ``NotReady('migration_029_missing')`` (503), never an empty table.

Chain facts come from ``plan_review`` (``ticker_context`` / ``ticker_chain_items``, `timeline-2`, the fills as the cutoff
knew them), the executable fields from ``entry_rules``, adoption from ``execution``. A holding whose ``holding_status``
row is missing today (the review stage failed as a whole) is computed on the fly and marked ``stale`` (R18); the
computed row is never stored. ``python -m investment_assistant.views.plan_table show --session D`` prints the three
groups for a check without the page.
"""
from __future__ import annotations
import investment_assistant.review.weekly as review_weekly
import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.analysis.units as analysis_units
from investment_assistant.execution import chains as execution_chains
from investment_assistant.execution import trades as execution_trades
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo

import argparse
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

from investment_assistant.core import tx as core_tx
from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.core import texts as labels
from investment_assistant.review import outcomes as review
from investment_assistant.core import tx as service
from investment_assistant.views.today import _brief_view
from investment_assistant.workflow.nodes import review as plan_review
from investment_assistant.core.constants import OPEN_UNIT_DAYS
from investment_assistant.analysis import synthesis
from investment_assistant.review import tracks
from investment_assistant.execution import timeline
from investment_assistant.execution import entry_rules
from investment_assistant.execution import adoption as execution
from investment_assistant.execution import plan_levels as plans_module
from investment_assistant.execution import positions
from investment_assistant.data.adapters import bars as bars_module

NY = ZoneInfo('America/New_York')
ENDED_STATES = timeline.ENDED_STATES
LAPSED_RENEWAL_TEXT = '再次触发未产生新版，本计划已到截止日，明日不可执行'     # M8 (full-review)
POOL_ORDER = {'core': 0, 'screen': 1, 'extended': 2}
INSTRUCTION_ORDER = {name: index for index, name in enumerate(('exit', 'sell_half', 'hold', 'no_plan'))}
OWN_LINK_SESSIONS = execution.LINKABLE_OWN_SESSIONS
OUTCOME_TEXT = {'maintain': '维持', 'revise': '修订', 'invalidate': '作废', 'skipped': '跳过', 'error': '出错'}
STATE_TEXT = {'entry': '入场期', 'holding': '持仓期', 'exiting': '退出期', 'expired': '已过期', 'invalidated': '已作废',
              'position_closed': '已平仓', 'superseded': '被取代', 'closed_by_decision': '旧决定不买'}


class NotReady(service.Unavailable):
    """Migration 029 has not run (HTTP 503): the table, history and holding views answer this, never an empty list."""

    def __init__(self, code: str = 'migration_029_missing'):
        super().__init__(code)
        self.code = code


def _require(conn) -> None:
    if not core_schema.executable_ready(conn):
        raise NotReady('migration_029_missing')


# ---------------------------------------------------------------- small helpers

def _ny_day(moment: datetime) -> date:
    return moment.astimezone(NY).date()


def _cutoff(session: date, now: datetime) -> datetime:
    """The cutoff a table of session S is read at: ``now`` for the latest session, else the end of S's New York day
    (a table of an older day does not know what was recorded after it)."""
    end = datetime.combine(session, time(23, 59, 59), tzinfo=NY)
    return min(now, end)


def _rule_names(rule_ids: list[str]) -> str:
    return '、'.join(labels.RULE_TEXT.get(r, r) for r in dict.fromkeys(rule_ids)) or '—'


def _plan_block(plan: dict[str, Any]) -> dict[str, Any]:
    """The ``plan`` object of a row: the levels as two-decimal strings, the entry type and the longest hold (the
    defaults with ``defaults_applied`` for a plan without execution fields)."""
    sizing = plan.get('sizing') or {}
    shares = sizing.get('shares')
    note = None
    if sizing.get('status') == 'no_equity':
        note = '无账户权益快照，未算股数'
    elif sizing.get('status') == 'no_room':
        note = '仓位上限内没有空间'
    entry_type = entry_rules.entry_type_of(plan)
    defaults = plan.get('entry_type') is None
    return {
        'plan_id': str(plan['id']), 'kind': plan['kind'], 'plan_version': plan['plan_version'], 'action': 'buy',
        'entry_type': entry_type, 'entry_type_text': entry_rules.ENTRY_TYPE_TEXT[entry_type],
        'entry_low': execution_trades._money(plan['entry_low']), 'entry_high': execution_trades._money(plan['entry_high']),
        'stop': execution_trades._money(plan['stop']), 'target1': execution_trades._money(plan['target1']),
        'shares': shares, 'shares_note': note, 'valid_from': plan['valid_from'], 'valid_until': plan['valid_until'],
        'max_hold_sessions': plan.get('max_hold_sessions') or plans_module.MAX_HOLD_DEFAULT,
        'status': plan['status'], 'defaults_applied': defaults,
    }


def _baseline_block(plan: dict[str, Any], active_chain: Any) -> dict[str, Any]:
    sizing = plan.get('sizing') or {}
    return {'plan_id': str(plan['id']), 'plan_version': plan['plan_version'], 'entry_low': execution_trades._money(plan['entry_low']),
            'entry_high': execution_trades._money(plan['entry_high']), 'stop': execution_trades._money(plan['stop']),
            'target1': execution_trades._money(plan['target1']), 'shares': sizing.get('shares'), 'status': plan['status'],
            'active_chain': active_chain}


def _notes_of(conn, ticker: str, *, chain_signal_date: date | None = None, plan_id=None, cutoff: datetime) -> list[dict]:
    rows = execution_repo.plan_notes_for(conn, ticker, chain_signal_date=chain_signal_date, plan_id=plan_id, cutoff=cutoff)
    return [{'id': n['id'], 'body': n['body'], 'created_at': n['created_at']} for n in rows if n['status'] == 'active']


def _pool_of_plan(conn, plan: dict[str, Any], cutoff: datetime) -> str:
    return review.pool_of((plan.get('inputs') or {}).get('context'),
                          data_repo.pool_entry_at(conn, plan['ticker'], plan['signal_date'], cutoff_at=cutoff))


def _head_output(conn, version: dict[str, Any]) -> dict[str, Any]:
    run = data_repo.agent_run_by_id(conn, version['agent_run_id'])
    return (run or {}).get('output') or {}


def _void_conditions(output: dict[str, Any], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The head version's invalidation conditions, each with the result of the newest review that judged it."""
    last: dict[int, str] = {}
    for row in reviews:
        for item in row.get('conditions') or []:
            if item.get('list') == 'invalidation' and isinstance(item.get('index'), int):
                last[item['index']] = item.get('result')
    return [{**item, 'last_result': last.get(index)} for index, item in enumerate(output.get('invalidation') or [])]


# ---------------------------------------------------------------- the plan table

def _end_of(item: dict[str, Any], session: date, *, segments: list[dict[str, Any]]) -> dict[str, Any]:
    """When and how an ended chain ended: ``{kind, at, text}`` (``at`` is the session it became terminal)."""
    state = item['state']
    chain, name = item['chain'], state['state']
    head = chain['head_plan']
    ref = state.get('basis_ref') or {}
    if name == 'expired':
        cap = timeline.entry_cap(chain, [r for r in item['facts']['retriggers']])
        at = head['valid_until'] if session >= head['valid_until'] else add_trading_days(cap, 1)
        return {'kind': name, 'at': at, 'text': '已过期（未执行）'}
    if name == 'invalidated':
        row = next((r for r in chain['reviews'] if r['id'] == ref.get('review_id')), None)
        at = row['session'] if row else session
        reason = (row or {}).get('reason') or state['basis']
        return {'kind': name, 'at': at, 'text': f'已作废：{reason}'}
    if name == 'position_closed':
        buy_ids = {b['id'] for b in item['buys']}
        closed = [sg['closed_at'] for sg in segments if sg['buy_trade_id'] in buy_ids]
        return {'kind': name, 'at': _ny_day(max(closed)) if closed else session, 'text': '已平仓'}
    if name == 'superseded':
        at = date.fromisoformat(ref['superseded_by']) if ref.get('superseded_by') else session
        return {'kind': name, 'at': at, 'text': '被取代'}
    decision = item['facts'].get('decision')
    at = _ny_day(decision['decided_at']) if decision else session
    return {'kind': name, 'at': at, 'text': '旧决定不买'}


def _how(end: dict[str, Any]) -> str:
    """How a chain ended, as a verb phrase after 「已于 <日期>」: 「过期未执行」「作废：…」「平仓」「被取代」."""
    how = end['text'].replace('已过期（未执行）', '过期未执行')
    return how[1:] if how.startswith('已') else how


def _within_window(at: date, session: date) -> bool:
    return at <= session and len(trading_days_between(at, session)) - 1 <= timeline.ENDED_WINDOW_SESSIONS


def _closed_r(conn, ticker: str, item: dict[str, Any], context: dict[str, Any]) -> float | None:
    """The actual R of a closed chain (the first linked fill's plan prices the risk), None when it cannot be told."""
    buys = item['buys']
    if not buys:
        return None
    ids = {b['id'] for b in buys}
    plans_by_trade = {b['id']: p for b in buys if (p := execution_chains._plan_of_buy(conn, b)) is not None}
    book = context['book']
    out = tracks.actual_track_v2(
        plans_by_trade=plans_by_trade, buys=buys, segments=[s for s in book['closed'] if s['buy_trade_id'] in ids],
        open_lots=[], last_close=None, splits=data_repo.split_events(conn, ticker), session=date.max,
        unit_valid_until=None)
    return out['r']


def _execution_fields(conn, item: dict[str, Any], *, session: date, chain_ended: bool, in_entry: bool) -> dict[str, Any]:
    """``status.execution`` (the chain level, R13), the first fill's date / size and what it followed."""
    chain = item['chain']
    versions = [execution_repo.plan_by_id(conn, v['plan_id']) for v in chain['versions']]
    for plan, version in zip(versions, chain['versions']):
        plan['version_no'] = version['version_no']
    code = execution.chain_status(versions, item['buys'], session=session, chain_ended=chain_ended,
                                  chain_in_entry=in_entry)
    out: dict[str, Any] = {'execution': code}
    live = [b for b in item['buys'] if b.get('voided_at') is None]
    if live:
        first = min(live, key=lambda b: (b['executed_at'], str(b['id'])))
        out['execution_detail'] = {'date': execution_trades._mmdd(_ny_day(first['executed_at'])),
                                   'quantity': format(Decimal(str(first['quantity'])).normalize(), 'f')}
        plans_by_id = {p['id']: p for p in versions}
        for buy in live:
            if buy.get('plan_id') is not None and buy['plan_id'] not in plans_by_id:
                found = execution_repo.plan_by_id(conn, buy['plan_id'])
                if found is not None:
                    plans_by_id[found['id']] = found
        followed = execution.followed(live, plans_by_id=plans_by_id,
                                      version_no_by_plan={p['id']: p['version_no'] for p in versions})
        if followed is not None:
            out['execution_basis'] = f"照 {followed['text']}"
    return out


def _latest_review(chain: dict[str, Any]) -> dict[str, Any] | None:
    effective = [r for r in chain['reviews'] if r['outcome'] in execution_repo.EFFECTIVE_REVIEW_OUTCOMES]
    if not effective:
        return None
    row = effective[-1]
    return {'outcome': row['outcome'], 'decided_by': row['decided_by'], 'reason': row['reason'],
            'session': row['session'], 'via': (row.get('detail') or {}).get('via'), 'trigger': row['trigger']}


def _chain_row(conn, ticker: str, item: dict[str, Any], *, group: str, session: date, cut: datetime,
               context: dict[str, Any], holding: dict[str, Any] | None, end: dict[str, Any] | None) -> dict[str, Any]:
    chain = item['chain']
    signal_date = chain['signal_date']
    versions, head_plan = chain['versions'], chain['head_plan']
    head_version = versions[-1]
    name = item['state']['state']
    numbers = {str(v['plan_id']): v['version_no'] for v in versions}
    retriggers = plan_review.retrigger_rows(conn, ticker, signal_date, chain['reviews'], numbers, cutoff=cut)
    output = _head_output(conn, head_version)
    today_reviews = [r for r in chain['reviews'] if r['session'] == session]
    last_today = today_reviews[-1] if today_reviews else None
    fired = [s['kind'] for s in (last_today.get('signals') or []) if s.get('fired')] if last_today else []
    revised_today = next((r for r in reversed(today_reviews) if r.get('new_plan_id') is not None), None)
    via = (revised_today.get('detail') or {}).get('via') if revised_today else None
    in_entry = name == 'entry'
    mode = 'holding' if name in ('holding', 'exiting') else 'entry'
    cap = timeline.entry_cap(chain, item['facts']['retriggers'])
    plan = _plan_block(head_plan)
    void = entry_rules.void_when(head_plan, conditions=_void_conditions(output, chain['reviews']),
                                 mode=mode, entry_cap=cap if mode == 'entry' else None)
    status: dict[str, Any] = {'label': '', 'latest_review': _latest_review(chain), 'today_signals': fired,
                              **_execution_fields(conn, item, session=session, chain_ended=name in ENDED_STATES,
                                                  in_entry=in_entry)}
    if head_version['version_no'] > 0:
        status['changes_text'] = [c['text'] for c in (output.get('changes') or []) if isinstance(c, dict) and c.get('text')]
        status['diff'] = head_version.get('diff') or []
    if group == 'entry':
        label = '入场期'
        if revised_today is not None and via == 'retrigger':
            label += ' · 今日修订（再次触发）'
        elif revised_today is not None:
            label += ' · 今日修订'
        elif head_plan['valid_until'] <= session:      # R5: renewed by a trigger today, but no new version came of it
            label += f' · {LAPSED_RENEWAL_TEXT}'
        elif 'retrigger' in fired:
            label += ' · 今日再次触发（未产生新版）'
        status['label'] = label
    elif group == 'holding':
        if name == 'exiting':
            since = (item['state'].get('basis_ref') or {}).get('exit_session')
            status['label'] = f'退出期 · 退出指令自 {since[5:] if since else "—"} 起'
        else:
            status['label'] = '持仓期'
    else:
        status['label'] = end['text']
        if end['kind'] == 'position_closed':
            r = _closed_r(conn, ticker, item, context)
            if r is not None:
                status['label'] = f"已平仓（{'+' if r >= 0 else ''}{r:.1f}R）"
    baseline_plan, merged = None, None
    if retriggers:
        merged = retriggers[-1]
        if merged.get('baseline_plan_id') is not None:
            baseline_plan = execution_repo.plan_by_id(conn, merged['baseline_plan_id'])
    own_baseline = execution_repo.rule_plan(conn, ticker, signal_date)
    shown = baseline_plan or own_baseline
    baseline = (_baseline_block(shown, {'ticker': ticker, 'signal_date': signal_date.isoformat()}
                                if baseline_plan is not None else False) if shown is not None else None)
    row = {
        'row_id': f'chain:{ticker}:{signal_date.isoformat()}', 'group': group, 'ticker': ticker,
        'pool': _pool_of_plan(conn, execution_repo.plan_by_id(conn, versions[0]['plan_id']), cut),
        'chain': {'signal_date': signal_date, 'version_no': head_version['version_no'],
                  'head_plan_id': str(head_plan['id']), 'state': name,
                  'label': execution_trades.chain_label(ticker, signal_date, head_version['version_no']),
                  'retriggers': [{'unit_signal_date': r['unit_signal_date'], 'rule_ids': r['rule_ids'],
                                  'version_no': r['version_no']} for r in retriggers]},
        'plan': plan, 'baseline': baseline,
        'open_scenarios': (entry_rules.scenarios(head_plan)
                           if group == 'entry' and head_plan['valid_until'] > session else []),
        'void_when': void, 'status': status, 'holding': holding,
        'notes': _notes_of(conn, ticker, chain_signal_date=signal_date, cutoff=cut),
        'links': {'plan': f"#plan/{head_plan['id']}", 'instrument': f'#instrument/{ticker}'},
    }
    if end is not None:
        row['ended_at'] = end['at']
    return row


def _holding_block(row: dict[str, Any], *, stale: bool) -> dict[str, Any]:
    keys = ('ticker', 'session', 'quantity', 'avg_cost', 'last_close', 'stop', 'stop_mode', 'target1', 't1_reached',
            'reached_1r', 'to_stop_pct', 'to_t1_pct', 'r_now', 'held_sessions', 'max_hold_sessions',
            'remaining_sessions', 'instruction', 'exit_reason', 'reason', 'review_id', 'chain_signal_date')
    out = {k: row.get(k) for k in keys}
    out['plan_id'] = str(row['plan_id']) if row.get('plan_id') is not None else None
    out['detail'] = row.get('detail') or {}
    if stale:
        out['stale'] = True
    return out


def _holding_rows(conn, session: date, *, cut: datetime, errors: dict[str, str],
                  point_in_time: bool = False) -> dict[str, dict[str, Any]]:
    """The day's ``holding_status`` rows by ticker; a held ticker without one gets a computed row marked stale."""
    stored = {r['ticker']: _holding_block(r, stale=False) for r in execution_repo.holding_status_for(conn, session=session)}
    for ticker in execution_chains._held_tickers(conn, cut):
        if ticker in stored:
            continue
        try:
            with conn.transaction():
                values = execution_chains._holding_row(conn, ticker, session, None, now=cut, skipped={}, unavailable=None,
                                                  point_in_time=point_in_time)
        except Exception as exc:        # noqa: BLE001 - one ticker's row never takes the table down (§13)
            errors[ticker] = f'holding: {str(exc) or type(exc).__name__}'
            continue
        if values is not None:
            stored[ticker] = _holding_block(values, stale=True)
    return stored


def _unit_row(conn, ticker: str, signal_date: date, plan: dict[str, Any], *, group: str, status: dict[str, Any],
              session: date, cut: datetime, note_text: str | None = None, ended_at: date | None = None) -> dict[str, Any]:
    """A row for a unit that has no Agent chain: the rule baseline is the plan."""
    block = _plan_block(plan)
    row = {
        'row_id': f'unit:{ticker}:{signal_date.isoformat()}', 'group': group, 'ticker': ticker,
        'pool': _pool_of_plan(conn, plan, cut), 'chain': None, 'plan': block, 'baseline': None,
        'open_scenarios': entry_rules.scenarios(plan) if group == 'entry' else [],
        'void_when': entry_rules.void_when(plan, conditions=[], mode='entry', entry_cap=None) if group != 'holding' else [],
        'status': status, 'holding': None, 'notes': _notes_of(conn, ticker, plan_id=plan['id'], cutoff=cut),
        'links': {'plan': f"#plan/{plan['id']}", 'instrument': f'#instrument/{ticker}'},
    }
    if ended_at is not None:
        row['ended_at'] = ended_at
    return row


def _chainless_holding_row(conn, ticker: str, hs: dict[str, Any], context: dict[str, Any], *, cut: datetime) -> dict[str, Any]:
    plan = execution_repo.plan_by_id(conn, hs['plan_id']) if hs.get('plan_id') else None
    label = {'rule': '规则基线', 'own': '自定计划'}.get((plan or {}).get('kind'), '计划')
    buys = [b for b in context['trades'] if b['side'] == 'buy' and b.get('voided_at') is None]
    status: dict[str, Any] = {'label': f'无 Agent 链，按{label}看护' if plan else '无计划持仓', 'execution': 'executed',
                              'latest_review': None, 'today_signals': []}
    if buys:
        first = min(buys, key=lambda b: (b['executed_at'], str(b['id'])))
        status['execution_detail'] = {'date': execution_trades._mmdd(_ny_day(first['executed_at'])),
                                      'quantity': format(Decimal(str(first['quantity'])).normalize(), 'f')}
    row = {
        'row_id': f'holding:{ticker}', 'group': 'holding', 'ticker': ticker,
        'pool': review.pool_of(None, data_repo.pool_entry_at(conn, ticker, hs['session'], cutoff_at=cut)),
        'chain': None, 'plan': _plan_block(plan) if plan is not None else None, 'baseline': None,
        'open_scenarios': [],
        'void_when': (entry_rules.void_when(plan, conditions=[], mode='holding', entry_cap=None) if plan is not None else []),
        'status': status, 'holding': hs,
        'notes': _notes_of(conn, ticker, plan_id=plan['id'], cutoff=cut) if plan is not None else [],
        'links': {'plan': f"#plan/{plan['id']}" if plan is not None else None, 'instrument': f'#instrument/{ticker}'},
    }
    return row


def _sort_key(row: dict[str, Any]) -> tuple:
    if row['group'] == 'entry':
        return (POOL_ORDER.get(row['pool'], 3), row['ticker'])
    if row['group'] == 'holding':
        return (INSTRUCTION_ORDER.get((row['holding'] or {}).get('instruction'), 9), row['ticker'])
    return (-(row.get('ended_at') or date.min).toordinal(), row['ticker'])


def plan_table(conn, *, session: date | None = None, include_extended: bool = False,
               now: datetime | None = None) -> dict[str, Any]:
    """The 「今日」 plan table: ``{session, groups: {entry, holding, ended}, other_units, counts, errors}`` (spec §9.1).

    Entry = chains in the entry phase at S (a retrigger is a line inside the chain's row, not a row of its own) and
    the buy units that opened no chain (Agent failed, passed or unavailable: the rule baseline is the plan, the label
    says why). Holding = every position, a chain's latest version or the plan of its fills, with the day's
    ``holding_status`` row (computed and marked ``stale`` when the review stage did not write it, R18). Ended = chains
    and baselines that ended within the last 5 sessions. ``other_units``: sell / watch / conflict units, read-only.
    The extended pool is left out unless ``include_extended``. A ticker that cannot be read lands in ``errors``."""
    _require(conn)
    now = now or datetime.now(UTC)
    latest = bars_module.latest_closed_session(now)
    session = session or latest
    pit = session < latest                # an older day is read as its own close knew it: versions, reviews, fills
    cut = _cutoff(session, now) if pit else now
    errors: dict[str, str] = {}
    entry: list[dict] = []
    holding: list[dict] = []
    ended: list[dict] = []
    hs_rows = _holding_rows(conn, session, cut=cut, errors=errors, point_in_time=pit)
    seen_tickers: set[str] = set()

    by_ticker: dict[str, list[dict]] = {}
    for chain in execution_repo.all_chains(conn):
        by_ticker.setdefault(chain['ticker'], []).append(chain)
    active_tickers: set[str] = set()
    for ticker in sorted(by_ticker):
        # collected locally and merged only when the ticker's savepoint succeeded: a half-built ticker leaves no rows
        found: list[dict] = []
        held_seen = False
        active = False
        try:
            with conn.transaction():
                context = execution_chains.ticker_context(conn, ticker, now=cut, point_in_time=pit)
                items = execution_chains.ticker_chain_items(conn, ticker, session=session, now=cut, context=context,
                                                       point_in_time=pit)
                held_items = [i for i in items if i['state']['state'] in ('holding', 'exiting')]
                for item in items:
                    name = item['state']['state']
                    signal_date = item['chain']['signal_date']
                    row = None
                    if name == 'entry':
                        row = _chain_row(conn, ticker, item, group='entry', session=session, cut=cut,
                                         context=context, holding=None, end=None)
                        active = True
                    elif name in ('holding', 'exiting'):
                        latest_held = max(held_items, key=lambda i: i['chain']['signal_date']) is item
                        hs = hs_rows.get(ticker)
                        attached = hs if (hs is not None and (hs.get('chain_signal_date') == signal_date
                                                              or (hs.get('chain_signal_date') is None and latest_held))) else None
                        row = _chain_row(conn, ticker, item, group='holding', session=session, cut=cut,
                                         context=context, holding=attached, end=None)
                        active = held_seen = True
                    elif name in ENDED_STATES:
                        end = _end_of(item, session, segments=context['book']['closed'])
                        if _within_window(end['at'], session):
                            row = _chain_row(conn, ticker, item, group='ended', session=session, cut=cut,
                                             context=context, holding=None, end=end)
                    if row is not None:
                        found.append(row)
        except Exception as exc:        # noqa: BLE001 - one ticker never takes the whole table down
            errors[ticker] = str(exc) or type(exc).__name__
            continue                    # its position, if any, is computed by the holding section below
        for row in found:
            (entry if row['group'] == 'entry' else holding if row['group'] == 'holding' else ended).append(row)
        if active:
            active_tickers.add(ticker)
        if held_seen:
            seen_tickers.add(ticker)

    # positions that belong to no chain: the plan their fills follow, or none
    for ticker, hs in sorted(hs_rows.items()):
        if ticker in seen_tickers:
            continue
        try:
            with conn.transaction():
                context = execution_chains.ticker_context(conn, ticker, now=cut, point_in_time=pit)
                holding.append(_chainless_holding_row(conn, ticker, hs, context, cut=cut))
        except Exception as exc:        # noqa: BLE001
            errors[ticker] = str(exc) or type(exc).__name__

    other = _units_without_chain(conn, session=session, cut=cut, entry=entry, ended=ended, pit=pit,
                                 active_tickers=active_tickers, errors=errors)
    groups = {'entry': entry, 'holding': holding, 'ended': ended}
    if not include_extended:       # the switch is for extended-pool units; a position is never hidden from 「今日」
        groups = {name: rows if name == 'holding' else [r for r in rows if r['pool'] != 'extended']
                  for name, rows in groups.items()}
        other = [u for u in other if not u.pop('_extended', False)]
    for unit in other:
        unit.pop('_extended', None)
    for rows in groups.values():
        rows.sort(key=_sort_key)
    return {'session': session, 'groups': groups, 'other_units': other,
            'counts': {name: len(rows) for name, rows in groups.items()}, 'errors': errors}


def _units_without_chain(conn, *, session: date, cut: datetime, entry: list[dict], ended: list[dict], pit: bool,
                         active_tickers: set[str], errors: dict[str, str]) -> list[dict[str, Any]]:
    """The undecided units of the last 14 days that have no row yet: buy units without a chain become entry rows (or
    ended rows once their baseline expired), the rest are the 「其他触发」 lines."""
    other: list[dict[str, Any]] = []
    for group in analysis_repo.open_candidate_groups(conn, since=session - timedelta(days=OPEN_UNIT_DAYS)):
        ticker, signal_date = group['ticker'], group['signal_date']
        if signal_date > session:
            continue
        try:
            with conn.transaction():
                held = positions.build(execution_repo.list_trades(conn, ticker, cutoff=cut),
                                       splits=data_repo.split_events(conn, ticker))['position'] is not None
                unit = synthesis.synthesize(ticker, signal_date, group['candidates'], holding=held, now=cut)
                extended = analysis_units._unit_is_extended(conn, ticker, signal_date, held=held, now=cut)
                brief = _brief_view(analysis_repo.latest_brief(conn, ticker, signal_date))
                shape = {'ticker': ticker, 'signal_date': signal_date, 'proposed_action': unit['proposed_action'],
                         'labels': unit['labels'], 'rule_ids': unit['rule_ids'], 'brief': brief,
                         '_extended': extended}
                if unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
                    other.append(shape)
                    continue
                if execution_repo.chain_versions(conn, ticker, signal_date):
                    continue                      # the unit opened a chain: its row is the chain's
                merged = execution_repo.retrigger_for_unit(conn, ticker, signal_date)
                merged_end = None
                if merged is not None:
                    target = execution_chains.read_chain_facts(
                        conn, {'ticker': ticker, 'signal_date': merged['chain_signal_date']}, session=session,
                        now=cut, point_in_time=pit)
                    if target is not None and target['state']['state'] in timeline.ACTIVE_STATES:
                        continue                  # merged into an active chain: a line inside that chain's row
                    if target is not None:
                        context = execution_chains.ticker_context(conn, ticker, now=cut, point_in_time=pit)
                        merged_end = _end_of(target, session, segments=context['book']['closed'])
                elif ticker in active_tickers:
                    other.append(shape)           # the ticker has an active chain this older unit never joined: a line only
                    continue
                plan = execution_repo.rule_plan(conn, ticker, signal_date)
                if plan is None or plan['status'] != 'ok':
                    other.append(shape)
                    continue
                context = execution_chains.ticker_context(conn, ticker, now=cut, point_in_time=pit)
                if review_weekly.unit_fills(conn, ticker, signal_date, context):
                    continue                      # a fill followed this baseline: the position's row shows it
                row = _no_chain_unit(conn, ticker, signal_date, plan, merged=merged, merged_end=merged_end, session=session,
                                     cut=cut)
                if row is None:
                    continue
                row['pool'] = 'extended' if extended else row['pool']
                (entry if row['group'] == 'entry' else ended).append(row)
        except Exception as exc:        # noqa: BLE001
            errors[ticker] = str(exc) or type(exc).__name__
    return other


def _no_chain_unit(conn, ticker: str, signal_date: date, plan: dict[str, Any], *, merged: dict | None,
                   merged_end: dict | None, session: date, cut: datetime) -> dict[str, Any] | None:
    """The row of a buy unit with no chain of its own: why the Agent gave no plan, or that its chain ended today."""
    runs = analysis_repo.agent_runs_for(conn, ticker, signal_date)
    latest = runs[-1] if runs else None
    if merged is not None:
        # the chain the unit joined is over: say how and when it ended (expired, invalidated, closed, superseded ...)
        ended = (f" {execution_trades._mmdd(merged_end['at'])} {_how(merged_end)}" if merged_end is not None else '')
        reason = (f"所并入的链 {ticker}·{execution_trades._mmdd(merged['chain_signal_date'])} 已于{ended}，以下为规则基线"
                  if merged_end is not None else '所并入的链已结束，以下为规则基线')
    elif latest is None:
        reason = 'Agent 尚未给出计划，以下为规则基线'
    elif latest['status'] == 'pass':
        reason = 'Agent 判断不建议买入（pass），以下为规则基线'
    else:
        why = analysis_agent_runner.agent_reason_text(latest.get('reason') or latest['status'])
        reason = f'Agent 未给出计划：{why}，以下为规则基线'
    if session < plan['valid_until']:
        return _unit_row(conn, ticker, signal_date, plan, group='entry', session=session, cut=cut,
                         status={'label': reason, 'execution': 'pending', 'latest_review': None, 'today_signals': []})
    if _within_window(plan['valid_until'], session):
        return _unit_row(conn, ticker, signal_date, plan, group='ended', session=session, cut=cut,
                         ended_at=plan['valid_until'],
                         status={'label': '已过期（未执行）', 'execution': 'not_executed', 'latest_review': None,
                                 'today_signals': []})
    return None


# ---------------------------------------------------------------- holding status

def holding_status_view(conn, *, session: date, now: datetime | None = None) -> list[dict[str, Any]]:
    """The day's holding-status rows, one per held ticker, ordered by instruction (exit first) then ticker. Stored rows
    as written; a held ticker whose row is missing is computed and marked ``stale`` (R18), never stored."""
    _require(conn)
    now = now or datetime.now(UTC)
    pit = session < bars_module.latest_closed_session(now)
    cut = _cutoff(session, now) if pit else now
    errors: dict[str, str] = {}
    rows = list(_holding_rows(conn, session, cut=cut, errors=errors, point_in_time=pit).values())
    rows.sort(key=lambda r: (INSTRUCTION_ORDER.get(r['instruction'], 9), r['ticker']))
    return rows


# ---------------------------------------------------------------- linkable plans

def linkable_plans(conn, ticker: str, *, executed_at: datetime) -> dict[str, Any]:
    """The plans a fill at ``executed_at`` may follow (spec §6.1): the head, as of the fill, of the ticker's active
    chain (default; an exiting chain is not offered, R10), the rule baselines whose validity covers the fill day, and
    the own plans of the last 5 sessions. ``{ticker, plans: [{plan_id, label, kind, chain_signal_date, version_no}],
    default_plan_id}``."""
    _require(conn)
    ticker = ticker.upper()
    day = _ny_day(executed_at)
    plans: list[dict[str, Any]] = []
    default_id = None
    # read the chain as the moment of the fill knew it: the version that was the head then, the state it was in then
    # (a fill during day D follows the plan table of the session before D: the chain's state is judged after that
    # session's close, so a plan whose last valid day is D is still in its entry period)
    found = execution_chains.active_chain_of(conn, ticker, session=add_trading_days(day, -1), now=executed_at,
                                        point_in_time=True)
    if found is not None and found['state']['state'] in timeline.REVIEWABLE_STATES:
        chain = found['chain']
        head = chain['versions'][-1]
        plan = chain['head_plan']
        plans.append({'plan_id': str(plan['id']), 'kind': 'agent', 'chain_signal_date': chain['signal_date'],
                      'version_no': head['version_no'],
                      'label': execution_trades.plan_label(plan, version_no=head['version_no'],
                                          state=STATE_TEXT[found['state']['state']])})
        default_id = str(plan['id'])
    since = add_trading_days(day, -OWN_LINK_SESSIONS)
    for plan in execution_repo.plans_for_ticker(conn, ticker, since=since):
        if plan['kind'] == 'rule' and plan['status'] == 'ok' and plan['valid_from'] <= day <= plan['valid_until']:
            plans.append({'plan_id': str(plan['id']), 'kind': 'rule', 'chain_signal_date': None, 'version_no': None,
                          'label': execution_trades.plan_label(plan)})
        elif plan['kind'] == 'own' and since <= plan['valid_from'] <= day:
            plans.append({'plan_id': str(plan['id']), 'kind': 'own', 'chain_signal_date': None, 'version_no': None,
                          'label': execution_trades.plan_label(plan)})
    return {'ticker': ticker, 'plans': plans, 'default_plan_id': default_id}


# ---------------------------------------------------------------- plan history

def _fill_text(trade: dict[str, Any], plans_text: str | None) -> str:
    side = '买入' if trade['side'] == 'buy' else '卖出'
    quantity = format(Decimal(str(trade['quantity'])).normalize(), 'f')
    link = f'，关联 {plans_text}' if plans_text else ('，未关联计划' if trade['side'] == 'buy' else '')
    return f"{side} {quantity} 股 @ {execution_trades._money(trade['price'])}{link}"


def _levels_text(plan: dict[str, Any]) -> str:
    return (f"入场 {execution_trades._money(plan['entry_low'])}–{execution_trades._money(plan['entry_high'])} · 止损 {execution_trades._money(plan['stop'])}"
            f" · T1 {execution_trades._money(plan['target1'])}")


def _diff_text(diff: list[dict[str, Any]] | None) -> str:
    names = {'entry': '入场区间', 'stop': '止损', 'target1': 'T1', 'valid_until': '有效期', 'entry_cap': '入场期上限',
             'entry_type': '入场类型', 'max_hold_sessions': '最长持有'}
    return '；'.join(f"{names.get(d.get('field'), d.get('field'))} {d.get('before')} → {d.get('after')}"
                    for d in diff or [])


def plan_history(conn, ticker: str, *, include_reviews: bool = False, include_baselines: bool = False,
                 now: datetime | None = None) -> dict[str, Any]:
    """Every plan chain of the ticker as a timeline (spec §10.3): ``{ticker, rows: [HistoryRow], chains}``.

    Rows are ordered by ``at``: per chain a ``chain_open`` row (why it opened again, with how the previous chain
    ended), its versions, the retriggers merged into it, the invalidations and exit instructions, the fills and a
    ``chain_end`` row. ``include_reviews`` adds the daily maintain / skipped / error reviews, ``include_baselines`` the
    rule baselines. ``chains`` summarises each chain (state, version count, end)."""
    _require(conn)
    ticker = ticker.upper()
    now = now or datetime.now(UTC)
    session = bars_module.latest_closed_session(now)
    context = execution_chains.ticker_context(conn, ticker, now=now)
    items = execution_chains.ticker_chain_items(conn, ticker, session=session, now=now, context=context)
    rows: list[tuple] = []
    chain_summaries: list[dict[str, Any]] = []
    previous_end: dict[str, Any] | None = None
    previous_key = None
    plan_text: dict[Any, str] = {}
    version_of: dict[Any, tuple] = {}

    def add(order: int, row: dict[str, Any]) -> None:
        rows.append((row['at'], order, len(rows), row))

    for item in items:
        chain = item['chain']
        signal_date = chain['signal_date']
        versions = chain['versions']
        numbers = {str(v['plan_id']): v['version_no'] for v in versions}
        for v in versions:
            plan_text[v['plan_id']] = f"Agent {execution_trades.chain_label(ticker, signal_date, v['version_no'])}"
            version_of[v['plan_id']] = (signal_date, v['version_no'])
        name = item['state']['state']
        end = _end_of(item, session, segments=context['book']['closed']) if name in ENDED_STATES else None
        if end is not None and end['kind'] == 'position_closed':
            r = _closed_r(conn, ticker, item, context)
            if r is not None:
                end = {**end, 'text': f"已平仓（实际 {'+' if r >= 0 else ''}{r:.1f}R）"}

        first = versions[0]
        candidates = data_repo.unit_candidates(conn, ticker, signal_date)
        rules_text = _rule_names([c['rule_id'] for c in candidates if c['action'] == 'buy'])
        if previous_end is None:
            tail = '此前没有计划链'
        else:
            gap = len(trading_days_between(previous_end['at'], signal_date)) - 1 if previous_end['at'] <= signal_date else 0
            how = _how(previous_end)                                       # 「已于 10-24 作废：…」, not 「已于 10-24 已作废」
            tail = (f"上一条链 {ticker}·{execution_trades._mmdd(previous_key)} 已于 {execution_trades._mmdd(previous_end['at'])} "
                    f"{how}，间隔 {max(gap, 0)} 个交易日")
        add(0, {'kind': 'chain_open', 'at': first['created_at'], 'session': _ny_day(first['created_at']),
                'chain_signal_date': signal_date, 'version_no': first['version_no'],
                'text': f"新开链 {ticker}·{execution_trades._mmdd(signal_date)}：规则 {rules_text}；{tail}"})

        reviews_by_new = {r['new_plan_id']: r for r in chain['reviews'] if r.get('new_plan_id') is not None}
        for version in versions:
            plan = execution_repo.plan_by_id(conn, version['plan_id'])
            output = _head_output(conn, version)
            changes = [{'field': c.get('field'), 'text': c['text']} for c in (output.get('changes') or [])
                       if isinstance(c, dict) and c.get('text')]
            produced = reviews_by_new.get(version['plan_id'])
            reason = produced['reason'] if produced else None
            text = f"第 {version['version_no']} 版：{_levels_text(plan)}"
            if version['diff']:
                text += f"；变化：{_diff_text(version['diff'])}"
            add(1, {'kind': 'version', 'at': version['created_at'], 'session': _ny_day(version['created_at']),
                    'chain_signal_date': signal_date, 'version_no': version['version_no'],
                    'levels': {k: execution_trades._money(plan[k]) for k in ('entry_low', 'entry_high', 'stop', 'target1')},
                    'validity': {'valid_from': plan['valid_from'], 'valid_until': plan['valid_until']},
                    'diff': version['diff'], 'changes': changes, 'reason': reason,
                    'decided_by': produced['decided_by'] if produced else None,
                    'signals_fired': ([s['kind'] for s in (produced.get('signals') or []) if s.get('fired')]
                                      if produced else None),
                    'text': text})
        for trig in plan_review.retrigger_rows(conn, ticker, signal_date, chain['reviews'], numbers, cutoff=now):
            produced = f"，产出第 {trig['version_no']} 版" if trig['version_no'] is not None else ''
            add(2, {'kind': 'retrigger', 'at': trig['created_at'], 'session': trig['unit_signal_date'],
                    'chain_signal_date': signal_date, 'version_no': trig['version_no'],
                    'signals_fired': ['retrigger'],
                    'text': f"{execution_trades._mmdd(trig['unit_signal_date'])} 再次触发（{_rule_names(trig['rule_ids'])}）→ 并入本链{produced}"})
        for review_row in execution_repo.chain_reviews(conn, ticker, signal_date):       # stored rows: they carry created_at
            outcome = review_row['outcome']
            shown = outcome == 'invalidate' or (include_reviews and outcome != 'revise') or \
                (include_reviews and review_row.get('new_plan_id') is None)
            if not shown:
                continue
            detail = review_row.get('detail') or {}
            if outcome == 'invalidate' and review_row['mode'] == 'holding' and review_row['timeline_version'] == timeline.TIMELINE_V2:
                why = timeline.EXIT_REASON_TEXT.get(detail.get('exit_reason'), detail.get('exit_reason') or '')
                text = f"退出指令（{why}）：{review_row['reason']}"
            else:
                text = f"复查 {review_row['session']}：{OUTCOME_TEXT.get(outcome, outcome)}，{review_row['reason']}"
            add(3, {'kind': 'review', 'at': review_row['created_at'], 'session': review_row['session'],
                    'chain_signal_date': signal_date, 'version_no': numbers.get(str(review_row['head_plan_id'])),
                    'reason': review_row['reason'], 'decided_by': review_row['decided_by'],
                    'signals_fired': [s['kind'] for s in (review_row.get('signals') or []) if s.get('fired')],
                    'text': text})
        if end is not None:
            add(5, {'kind': 'chain_end', 'at': datetime.combine(end['at'], time(23, 59), tzinfo=NY),
                    'session': end['at'], 'chain_signal_date': signal_date, 'end': {'kind': end['kind'], 'text': end['text']},
                    'text': end['text']})
            previous_end, previous_key = end, signal_date
        chain_summaries.append({'signal_date': signal_date, 'state': name, 'versions': len(versions),
                                'ended_at': end['at'] if end else None,
                                'end': {'kind': end['kind'], 'text': end['text']} if end else None})

    for trade in context['trades']:
        if trade.get('voided_at') is not None:
            continue
        link = plan_text.get(trade.get('plan_id'))
        if link is None and trade.get('plan_id') is not None:
            linked = execution_repo.plan_by_id(conn, trade['plan_id'])
            link = {'rule': '规则基线', 'own': '自定计划'}.get((linked or {}).get('kind'))
        chain_day = version_of.get(trade.get('plan_id'), (None,))[0]
        if chain_day is None and trade.get('plan_id') is not None:
            key = context['plan_chain'].get(trade['plan_id'])
            chain_day = key[1] if key else None
        add(4, {'kind': 'fill', 'at': trade['executed_at'], 'session': _ny_day(trade['executed_at']),
                'chain_signal_date': chain_day, 'text': _fill_text(trade, link)})
    if include_baselines:
        for plan in execution_repo.plans_for_ticker(conn, ticker, since=date.min):
            if plan['kind'] != 'rule':
                continue
            add(1, {'kind': 'version', 'at': plan['created_at'], 'session': plan['signal_date'],
                    'chain_signal_date': None, 'version_no': None,
                    'levels': {k: execution_trades._money(plan[k]) for k in ('entry_low', 'entry_high', 'stop', 'target1')},
                    'validity': {'valid_from': plan['valid_from'], 'valid_until': plan['valid_until']},
                    'text': f"规则基线 {plan['signal_date']}：{_levels_text(plan)}"})
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return {'ticker': ticker, 'rows': [r[3] for r in rows], 'chains': chain_summaries}


# ---------------------------------------------------------------- wrappers for the routes

def _view(fn: Callable[[Any], Any]) -> Any:
    with core_tx.read_only_tx() as conn:
        return fn(conn)


def table_view(*, now: datetime, session: date | None = None, include_extended: bool = False) -> dict[str, Any]:
    return _view(lambda conn: plan_table(conn, session=session, include_extended=include_extended, now=now))


def history_view(ticker: str, *, now: datetime, include_reviews: bool = False,
                 include_baselines: bool = False) -> dict[str, Any]:
    return _view(lambda conn: plan_history(conn, ticker.strip().upper(), include_reviews=include_reviews,
                                           include_baselines=include_baselines, now=now))


def holding_view(*, now: datetime, session: date | None = None) -> dict[str, Any]:
    day = session or bars_module.latest_closed_session(now)
    return {'session': day, 'rows': _view(lambda conn: holding_status_view(conn, session=day, now=now))}


def linkable_view(ticker: str, executed_at: datetime) -> dict[str, Any]:
    return _view(lambda conn: linkable_plans(conn, ticker, executed_at=executed_at))


def notes_view(*, ticker: str | None, chain_signal_date: date | None = None, plan_id=None) -> dict[str, Any]:
    def read(conn):
        _require(conn)
        nonlocal ticker
        if not ticker:                    # a plan knows its ticker
            plan = execution_repo.plan_by_id(conn, plan_id) if plan_id else None
            if plan is None:
                raise LookupError('plan not found')
            ticker = plan['ticker']
        rows = execution_repo.plan_notes_for(conn, ticker, chain_signal_date=chain_signal_date, plan_id=plan_id)
        return {'notes': [n for n in rows if n['status'] == 'active'], 'all': rows}
    out = _view(read)
    return {'notes': out['notes']}


# ---------------------------------------------------------------- command line

def _line(row: dict[str, Any]) -> str:
    plan, chain = row['plan'], row['chain']
    head = chain['label'] if chain else row['ticker']
    parts = [f"{row['ticker']:<6}", head]
    if plan is not None:
        parts += [plan['entry_type_text'], f"{plan['entry_low']}–{plan['entry_high']}", f"止损 {plan['stop']}",
                  f"T1 {plan['target1']}", f"有效至 {plan['valid_until']}"]
    if row['group'] == 'holding' and row['holding'] is not None:
        h = row['holding']
        parts.append(f"{h['instruction']}{'（现算）' if h.get('stale') else ''}：{h['reason']}")
    else:
        parts.append(row['status']['label'])
    return '  '.join(str(p) for p in parts)


def show(session: date | None, out: Callable[[str], None] = print, *, now: datetime | None = None) -> int:
    """Print the three groups of the plan table (read-only). Exit 0, 2 when the database or migration 029 is missing."""
    now = now or datetime.now(UTC)
    try:
        view = _view(lambda conn: plan_table(conn, session=session, now=now))
    except service.Unavailable as exc:
        out(f'计划表不可用：{exc}')
        return 2
    out(f"计划表 {view['session']}")
    for title, key in (('入场期', 'entry'), ('持仓中', 'holding'), ('已结束', 'ended')):
        rows = view['groups'][key]
        out(f'{title} {len(rows)}')
        for row in rows:
            out('  ' + _line(row))
    for unit in view['other_units']:
        out(f"其他触发  {unit['ticker']} {unit['signal_date']} 建议 {unit['proposed_action']}")
    for ticker, reason in view['errors'].items():
        out(f'读取失败  {ticker}  {reason}')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.views.plan_table',
                                     description='v2.9 计划表：只读，打印入场期 / 持仓中 / 已结束三组。')
    commands = parser.add_subparsers(dest='command', required=True)
    shower = commands.add_parser('show', help='打印某一交易日的计划表')
    shower.add_argument('--session', type=date.fromisoformat, help='交易日 YYYY-MM-DD，默认最近一个已收盘交易日')
    args = parser.parse_args(argv)
    return show(args.session, print)


if __name__ == '__main__':
    sys.exit(main())
