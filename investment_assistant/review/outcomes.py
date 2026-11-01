"""Shadow outcomes, trade reviews and weekly snapshots (design §7).

Descriptive only: counts, means, medians and raw values. No significance tests,
no claim that a rule works. Prices: entry at the next session's open adjusted by
that day's adj_close/close factor, exits at adj_close, so dividends and splits
inside a window do not distort returns.
"""
from __future__ import annotations
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.review.repo as review_repo

import statistics
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.execution import ledger

CALC_VERSION = 'review-8'          # v2.9 D30: executions instead of decisions, control group = all units (review-7: stats.learning)
SHADOW_MIN_SAMPLE = 5              # v2.5 §9.1: agent_summary gives no mean / median below this
WINDOWS = (5, 10, 20)
NY = ZoneInfo('America/New_York')


def _by_date(bars: list[dict[str, Any]]) -> dict[date, dict[str, Any]]:
    return {b['price_date']: b for b in bars}


def _adj_open(bar: dict[str, Any]) -> float:
    return bar['open'] * bar['adj_close'] / bar['close']


def _session(ts: datetime) -> date:
    return ts.astimezone(NY).date()


def _window_return(index, entry_day: date, sessions: int) -> tuple[date, float] | None:
    exit_day = add_trading_days(entry_day, sessions - 1)
    entry, exit_ = index.get(entry_day), index.get(exit_day)
    if entry is None or exit_ is None:
        return None
    return exit_day, exit_['adj_close'] / _adj_open(entry) - 1


def shadow_outcome(unit: dict[str, Any], bars: list[dict[str, Any]], spy_bars: list[dict[str, Any]]) -> dict[str, Any]:
    """What following the unit's proposed action from the next open would have done."""
    stock, spy = _by_date(bars), _by_date(spy_bars)
    entry_day = add_trading_days(unit['signal_date'], 1)
    if entry_day not in stock:
        return {'status': 'pending_entry', 'windows': {}}
    action = unit['proposed_action']
    windows: dict[str, Any] = {}
    for n in WINDOWS:
        own, bench = _window_return(stock, entry_day, n), _window_return(spy, entry_day, n)
        if own is None or bench is None:
            windows[str(n)] = {'status': 'immature'}
            continue
        exit_day, ret = own
        if action == 'watch':
            windows[str(n)] = {'status': 'mature', 'exit_date': exit_day.isoformat(), 'move': ret}
            continue
        sign = 1 if action == 'buy' else -1  # sell: the move avoided
        windows[str(n)] = {'status': 'mature', 'exit_date': exit_day.isoformat(), 'return': sign * ret,
                           'spy_return': sign * bench[1], 'excess': sign * (ret - bench[1])}
    return {'status': 'entered', 'entry_date': entry_day, 'entry_price': stock[entry_day]['open'],
            'direction': action, 'windows': windows}


def trade_segment_review(segment: dict[str, Any], bars: list[dict[str, Any]], spy_bars: list[dict[str, Any]]) -> dict[str, Any]:
    opened, closed = _session(segment['opened_at']), _session(segment['closed_at'])
    sessions = trading_days_between(opened, closed)
    stock, spy = _by_date(bars), _by_date(spy_bars)
    cost = segment['quantity'] * segment['entry_cost_per_share']
    out = {'buy_trade_id': str(segment['buy_trade_id']), 'sell_trade_id': str(segment['sell_trade_id']),
           'opened': opened.isoformat(), 'closed': closed.isoformat(), 'holding_sessions': len(sessions),
           'quantity': str(segment['quantity']), 'realized_pnl': float(segment['realized_pnl']),
           'return': float(segment['realized_pnl'] / cost) if cost else None,
           'spy_return': None, 'max_favorable': None, 'max_adverse': None}
    if opened in spy and closed in spy:
        out['spy_return'] = spy[closed]['adj_close'] / _adj_open(spy[opened]) - 1
    if opened in stock and all(d in stock for d in sessions):
        base = _adj_open(stock[opened])
        path = [stock[d]['adj_close'] / base - 1 for d in sessions]
        out['max_favorable'], out['max_adverse'] = max(0.0, *path), min(0.0, *path)
    return out


def open_position_review(ticker: str, position: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    last = bars[-1]
    cost = Decimal(position['avg_cost'])
    return {'ticker': ticker, 'quantity': str(position['quantity']), 'avg_cost': float(cost),
            'last_close': last['close'], 'as_of': last['price_date'].isoformat(),
            'unrealized_pnl': float(position['quantity'] * (Decimal(str(last['close'])) - cost)),
            'unrealized_return': last['close'] / float(cost) - 1}


def _summary(values: list[float], count: int) -> dict[str, Any]:
    return {'count': count, 'mature': len(values),
            'mean': statistics.fmean(values) if values else None,
            'median': statistics.median(values) if values else None,
            'values': [round(v, 6) for v in values]}


def _grouped(groups: dict[str, list[dict[str, Any]]], outcomes) -> dict[str, Any]:
    result = {}
    for key, units in groups.items():
        result[key] = {}
        for n in WINDOWS:
            values = [w['excess'] for u in units
                      if (w := outcomes.get(u['id'], {}).get('windows', {}).get(str(n), {})).get('excess') is not None]
            result[key][str(n)] = _summary(values, len(units))
    return result


def is_first_version(entry: dict[str, Any]) -> bool:
    """v2.5 §9.1 / D17: an agent plan is its chain's first version (agent-1 plans have no chain
    and count as their own first version)."""
    chain = entry.get('chain')
    return chain is None or chain.get('version_no') in (0, None)


def is_latest_version(entry: dict[str, Any]) -> bool:
    """The chain's head when the view was built (agent-1 plans are their own latest version)."""
    chain = entry.get('chain')
    return chain is None or bool(chain.get('is_head'))


def _plan_summary(plan_outcomes: list[dict[str, Any]], week_start: date, week_end: date) -> dict[str, Any]:
    """Per plan kind: count, expired, R mean and median. v2.5 D17 (review-6 / monthly-3): ``agent``
    counts each chain's first version only, ``agent_latest`` each chain's head at generation time."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for entry in plan_outcomes:
        if not week_start <= entry['signal_date'] <= week_end:
            continue
        if entry['kind'] != 'agent':
            by_kind.setdefault(entry['kind'], []).append(entry['outcome'])
            continue
        if is_first_version(entry):
            by_kind.setdefault('agent', []).append(entry['outcome'])
        if is_latest_version(entry):
            by_kind.setdefault('agent_latest', []).append(entry['outcome'])
    out = {}
    for kind, outcomes in by_kind.items():
        rs = [o['r'] for o in outcomes if o.get('r') is not None]
        out[kind] = {'count': len(outcomes), 'expired': sum(1 for o in outcomes if o['status'] == 'expired'),
                     'voided': sum(1 for o in outcomes if o['status'] == 'voided'),
                     'r_mean': statistics.fmean(rs) if rs else None,
                     'r_median': statistics.median(rs) if rs else None}
    return out


def shadow_block(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """One summary block over plan-shadow outcomes: ``determined`` = outcomes with an R; mean and median
    only from ``SHADOW_MIN_SAMPLE`` determined outcomes on. Shared by the plan-shadow view's
    ``agent_summary`` and the my-knowledge performance look-back (v2.7 D26)."""
    rs = [o['r'] for o in outcomes if o.get('r') is not None]
    enough = len(rs) >= SHADOW_MIN_SAMPLE
    return {'count': len(outcomes), 'determined': len(rs),
            'expired': sum(1 for o in outcomes if o.get('status') == 'expired'),
            'r_mean': statistics.fmean(rs) if enough else None,
            'r_median': statistics.median(rs) if enough else None}


def shadow_agent_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """v2.5 §9.1 ``agent_summary`` of the plan-shadow view: ``first`` (each chain's first version) and
    ``latest`` (each chain's current head) over the agent rows; ``determined`` = rows with an R; mean
    and median only from ``SHADOW_MIN_SAMPLE`` determined rows on."""
    agent = [row for row in rows if row.get('kind') == 'agent']
    return {'first': shadow_block([row['outcome'] for row in agent if is_first_version(row)]),
            'latest': shadow_block([row['outcome'] for row in agent if is_latest_version(row)])}


def pool_of(context: dict | None, entry: dict | None) -> str:
    """Pool of a plan: the frozen context wins, else the tier recorded at the signal date.

    'screen' comes first (a screened pick stays screen even after entering the main
    list); then only 'extended' lands in the extended pool — unknown and legacy
    (P1) plans count as core. Old contexts and entries without a source keep the
    two-way split.
    """
    context = context or {}
    if context.get('source') == 'screen':
        return 'screen'
    entry = entry or {}
    if not context and entry.get('source') == 'screen':
        return 'screen'
    tier = context.get('pool_tier') or entry.get('tier')
    return 'extended' if tier == 'extended' else 'core'


def pool_summary(triggers: list[dict], plan_outcomes: list[dict]) -> dict:
    """Per pool and buy rule: trigger count, plan count, expired, filled and R stats.

    A plan counts once for each of its buy rules; `filled` covers open and closed outcomes.
    Only rule plans count (spec §9): an own plan on the same unit would double it.
    """
    rows: dict[tuple, dict] = {}

    def row(pool: str, rule_id: str) -> dict:
        return rows.setdefault((pool, rule_id), {'triggers': 0, 'plans': 0, 'expired': 0, 'filled': 0, 'r': []})

    for trigger in triggers:
        row(trigger['pool'], trigger['rule_id'])['triggers'] += 1
    for plan in plan_outcomes:
        if plan.get('kind', 'rule') != 'rule':
            continue
        outcome = plan.get('outcome') or {}
        for rule_id in plan.get('rule_ids') or []:
            r = row(plan['pool'], rule_id)
            r['plans'] += 1
            if outcome.get('status') == 'expired':
                r['expired'] += 1
            if outcome.get('status') in ('open', 'closed'):
                r['filled'] += 1
            if outcome.get('r') is not None:
                r['r'].append(outcome['r'])
    out: dict[str, dict] = {}
    for (pool, rule_id), r in rows.items():
        out.setdefault(pool, {})[rule_id] = {
            'triggers': r['triggers'], 'plans': r['plans'], 'expired': r['expired'], 'filled': r['filled'],
            'r_mean': statistics.fmean(r['r']) if r['r'] else None,
            'r_median': statistics.median(r['r']) if r['r'] else None}
    return out


EXECUTION_GROUPS = ('adopted', 'out_of_validity', 'not_executed')
LEGACY_PLAN_LABEL = {'rule': '规则', 'own': '自定', 'agent': 'Agent 首版', 'agent_latest': 'Agent 最新版'}
LEGACY_SHADOW_TEXT = '旧计划 · plan-shadow-1'


def shadow_plan_summaries(plan_outcomes: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    """The plan-shadow part of a `review-8` / `monthly-4` snapshot (v2.9 §5.6): every plan of the period, each shadow
    version summarised on its own and never mixed.

    ``plans``: the plans with execution fields (`plan-shadow-2`) under the usual kind keys, then the older plans
    (`plan-shadow-1`) under keys that carry their basis (「规则（旧计划 · plan-shadow-1）」), so the page lists them all
    and says which shadow produced each row; ``plans_v1`` / ``plans_counterfactual`` / ``shadow_versions`` are the
    same split for machines. An entry without ``shadow_version`` counts as `plan-shadow-2`."""
    v2 = [p for p in plan_outcomes if p.get('shadow_version', 'plan-shadow-2') == 'plan-shadow-2']
    v1 = [p for p in plan_outcomes if p.get('shadow_version', 'plan-shadow-2') != 'plan-shadow-2']
    old = _plan_summary(v1, start, end)
    counterfactual = [{**p, 'outcome': p['shadow_v1']} for p in v2 if p.get('shadow_v1') is not None]
    return {
        'plans': {**_plan_summary(v2, start, end),
                  **{f'{LEGACY_PLAN_LABEL.get(kind, kind)}（{LEGACY_SHADOW_TEXT}）': block for kind, block in old.items()}},
        'plans_v1': old, 'plans_counterfactual': _plan_summary(counterfactual, start, end),
        'shadow_versions': {'plans': 'plan-shadow-2', 'plans_v1': 'plan-shadow-1',
                            'plans_counterfactual': 'plan-shadow-1'}}


def weekly_stats(week_start: date, units: list[dict[str, Any]], outcomes: dict[Any, dict[str, Any]],
                 trades: list[dict[str, Any]], *, segments: list[dict[str, Any]], entry_opens: dict[Any, float],
                 fills: list[dict[str, Any]] | None = None, unplanned: list[dict[str, Any]] | None = None,
                 plan_outcomes: list[dict[str, Any]] | None = None, discipline: dict[str, Any] | None = None,
                 by_pool: dict | None = None, by_pool_v1: dict | None = None, tracks: dict[str, Any] | None = None,
                 learning: dict[str, Any] | None = None) -> dict[str, Any]:
    """`review-8` snapshot (v2.9 D30) for the units whose signal_date falls in the week; values are excess returns
    vs SPY.

    ``units`` are ALL the week's units (the control group, D26: every ticker-day with candidates, not only the
    decided ones), each ``{id, ticker, signal_date, proposed_action, rule_ids, execution}`` with ``execution`` one of
    ``adopted`` / ``out_of_validity`` / ``not_executed`` (`execution-1`) or ``not_applicable`` (a sell / watch /
    conflict unit nobody traded: it counts in ``by_rule`` and the control group, not in the execution groups). ``units.executed`` / ``not_executed``
    count them; ``by_execution`` groups the excess returns by it, plus an ``unplanned`` group that only counts the
    week's plan-less buys (they have no signal to replay). ``fills`` are the week's linked buys
    ``{trade, unit_id, plan_created_at}``: the execution list prices each against the unit's T+1 open and measures
    the delay from the moment the linked plan version was generated (not from a decision). ``unplanned`` are the
    buys without any plan link, ``unplanned_trades`` their ids. ``learning`` (``learning_stats``) is frozen under
    ``stats['learning']``; without it the key is absent."""
    week_end = week_start + timedelta(days=6)
    fills, unplanned = fills or [], unplanned or []
    by_rule: dict[str, list] = {}
    by_execution: dict[str, list] = {}
    for u in units:
        for rule_id in dict.fromkeys(u['rule_ids']):
            by_rule.setdefault(rule_id, []).append(u)
        if u['execution'] in EXECUTION_GROUPS:         # a sell / watch / conflict unit nobody traded has none
            by_execution.setdefault(u['execution'], []).append(u)
    executed = sum(len(by_execution.get(name, [])) for name in ('adopted', 'out_of_validity'))

    execution = []
    for fill in fills:
        trade = fill['trade']
        entry = entry_opens.get(fill['unit_id']) or outcomes.get(fill['unit_id'], {}).get('entry_price')
        created = fill.get('plan_created_at')
        execution.append({'trade_id': str(trade['id']), 'unit': str(fill['unit_id']), 'ticker': trade['ticker'],
                          'slippage': float(trade['price']) / entry - 1 if entry else None,
                          'delay_hours': ((trade['executed_at'] - created).total_seconds() / 3600
                                          if created is not None else None)})
    grouped = {name: by for name, by in _grouped(by_execution, outcomes).items()}
    grouped['unplanned'] = {str(n): _summary([], len(unplanned)) for n in WINDOWS}
    stats = {
        'calc_version': CALC_VERSION,
        'week': {'start': week_start.isoformat(), 'end': week_end.isoformat()},
        'units': {'executed': executed, 'not_executed': len(by_execution.get('not_executed', []))},
        'by_rule': _grouped(by_rule, outcomes),
        'by_execution': grouped,
        'execution': execution,
        'unplanned_trades': [str(t['id']) for t in unplanned],
        'closed_trades': segments,
        **shadow_plan_summaries(plan_outcomes or [], week_start, week_end),
        'by_pool': by_pool or {},
        'by_pool_v1': by_pool_v1 or {},
        'discipline': discipline or {},
        'tracks': tracks,
        'note': '描述性统计，样本小且重叠，不代表规则有效。',
    }
    if learning is not None:
        stats['learning'] = learning
    return stats


def learning_stats(*, week_start: date, week_end: date, cards: list[dict[str, Any]],
                   perf_by_item: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """The weekly 「你学到了什么」 (v2.7 spec §10, since ``review-7``; ``review-8`` names the card line ``execution_text``), pure.

    ``cards``: one dict per review card with ``card_id, ticker, end_kind, end_text, end_session, execution_text,
    actual_r, filled, rule_r, agent_r, status, headline`` and ``discipline`` (the frozen ``facts.discipline`` totals);
    ``execution_text`` is a retro-2 card's 「你的执行」 wording, a retro-1 card's old decision wording (review-8);
    only those whose ``end_session`` falls in the week count, ordered by end day. ``perf_by_item``: knowledge
    id -> ``{ref (newest version), title, summary}`` where ``summary`` is ``knowledge_perf.summarize`` over all of
    the item's samples; an item is "tested this week" when at least one sample row's ``outcome_date`` lies in the
    week, ``new_outcomes`` counts those rows and ``sample`` is the whole item's block."""
    flag_order = ('chase', 'early_take', 'held_loser', 'unplanned')
    in_week = sorted((c for c in cards if week_start <= _day(c['end_session']) <= week_end),
                     key=lambda c: (_day(c['end_session']), c['card_id']))
    discipline = {'cards': len(in_week), **{flag: 0 for flag in flag_order}, 'pending': 0}
    out_cards = []
    for card in in_week:
        totals = card.get('discipline') or {}
        for flag in flag_order:
            discipline[flag] += int(totals.get(flag) or 0)
        discipline['pending'] += int(totals.get('pending') or 0)
        out_cards.append({'card_id': card['card_id'], 'ticker': card['ticker'], 'end_kind': card['end_kind'],
                          'end_text': card['end_text'], 'execution_text': card['execution_text'],
                          'actual_r': card.get('actual_r'), 'filled': bool(card.get('filled')),
                          'rule_r': card.get('rule_r'),
                          'agent_r': card.get('agent_r'),
                          'flags': [flag for flag in flag_order if totals.get(flag)],
                          'status': card['status'], 'headline': card.get('headline')})
    counts = {'total': len(in_week), **{status: sum(1 for c in in_week if c['status'] == status)
                                        for status in ('reviewed', 'to_review', 'drafting', 'draft_failed')}}
    knowledge = []
    for knowledge_id in sorted(perf_by_item):
        item = perf_by_item[knowledge_id]
        rows = (item.get('summary') or {}).get('rows') or []
        new = sum(1 for row in rows if row.get('outcome_date') and week_start <= _day(row['outcome_date']) <= week_end)
        if new:
            knowledge.append({'ref': item['ref'], 'title': item['title'], 'new_outcomes': new,
                              'sample': item['summary']['sample']})
    return {'discipline': discipline, 'cards': out_cards, 'counts': counts, 'knowledge': knowledge}


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def save_weekly(conn, week_start: date, stats: dict[str, Any], *, notes: str, supersedes_id) -> dict[str, Any]:
    """Freeze a snapshot. A week's later snapshot must supersede the current head."""
    if week_start.isoweekday() != 1:
        raise ledger.LedgerError('week_start must be a Monday')
    core_dbutil.lock(conn, f'weekly:{week_start}')
    head = review_repo.weekly_head(conn, week_start)
    if (head['id'] if head else None) != supersedes_id:
        raise ledger.Conflict('this week already has a newer review; supersede the latest one')
    return review_repo.insert_weekly_review(conn, week_start, CALC_VERSION, stats, notes or '', supersedes_id)


def list_weekly(conn, limit: int = 20) -> list[dict[str, Any]]:
    return review_repo.weekly_heads(conn, limit)
