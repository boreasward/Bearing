"""Assemble the daily push payload (design §7.2). Pure data, no rendering.

Input is what service already read: today()'s units (with plan) and holdings,
plus the batch summary. Nothing here talks to a channel or the database.
"""
from __future__ import annotations
from investment_assistant.data import families as data_families

from datetime import date
from typing import Any

from investment_assistant.data.calendar import add_trading_days

EVIDENCE_TEXT = '证据：未验证'  # Phase 0 Gate HOLD
AGENT_BRIEF_VERSIONS = ('agent-1', 'agent-2', 'agent-3')


def _agent_field(agent: dict[str, Any] | None) -> dict[str, Any] | None:
    """One unit's agent line data: levels, shares and status from the agent plan, the
    proposal's summary and the counter's first risk (P3 §10.2); v2.5 §10.3 adds the chain's
    version number, the first per-thesis counter and whether the unit is an agent-1 one."""
    if agent is None:
        return None
    plan = agent.get('plan') or {}
    sizing = plan.get('sizing') or {}
    counter = agent.get('counter') or {}
    risks = counter.get('risks') or []
    first_risk = risks[0].get('text') if risks and isinstance(risks[0], dict) else None
    counters = counter.get('counter') or []
    legacy = bool(agent.get('legacy')) or agent.get('agent_version') == 'agent-1'
    if legacy:
        first_counter = first_risk                       # agent-1 has no per-thesis counters
    else:
        first_counter = counters[0].get('text') if counters and isinstance(counters[0], dict) else None
    chain = agent.get('chain') or {}
    latest = chain.get('latest_review') or {}
    return {
        'status': agent.get('status'), 'model': agent.get('model'), 'reason': agent.get('reason'),
        'summary': (agent.get('output') or {}).get('summary'),
        'first_risk': first_risk,
        'entry_low': plan.get('entry_low'), 'entry_high': plan.get('entry_high'),
        'stop': plan.get('stop'), 'target1': plan.get('target1'),
        'shares': sizing.get('shares'), 'sizing_status': sizing.get('status'),
        'plan_status': plan.get('status'), 'invalid_reasons': plan.get('invalid_reasons') or [],
        'version_no': chain.get('version_no'), 'first_counter': first_counter, 'legacy': legacy,
        'chain_state': chain.get('state'),
        'invalidated_reason': latest.get('reason') if chain.get('state') == 'invalidated' else None,
        # v2.9 §11: the executable fields of an agent-3 version and the chain it belongs to
        'entry_type': plan.get('entry_type'), 'max_hold_sessions': plan.get('max_hold_sessions'),
        'valid_from': plan.get('valid_from'), 'valid_until': plan.get('valid_until'),
        'chain_signal_date': chain.get('signal_date'),
        'retrigger': chain.get('retrigger'),
    }


def build(*, session: date, units: list[dict[str, Any]], holdings: list[dict[str, Any]], summary: dict[str, Any],
          last_push_failed: bool, public_url: str, extended_plans: int = 0,
          market: dict[str, Any] | None = None, agent_summary: dict[str, Any] | None = None,
          screen: dict[str, Any] | None = None, plan_updates: list[dict[str, Any]] | None = None,
          retro: dict[str, Any] | None = None, fetch_status: list[dict[str, Any]] | None = None,
          holding_status: list[dict[str, Any]] | None = None, merged: set[tuple] | None = None,
          session_units: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """{'session', 'overview', 'plans', 'plan_updates', 'holdings', 'pending', 'errors', 'errors_by_source'}
    for the day's push. `units` are the new-plan entries the service assembled (v2.9 §11: each entry-phase chain
    and the day's rule baselines; there is no 「undecided」 notion any more). `plan_updates` are the review rows of
    the D25 window, each with its chain's `pool`, `from_version` / `to_version`, `changes` (texts), the new
    version's `levels` and `valid_until`. `holding_status` (v2.9 §7.3, §11: the day's `holding_status` rows) makes
    the holdings section one status line per position, kept on quiet days too; a held ticker without a row falls
    back to the old holding fields (`holdings`) and says so. `merged` is the set of `(ticker, signal_date)` units
    the `plan` node merged into an active chain (not counted as missing Agent suggestions); `session_units` are
    the day's units the overview's Agent counts read (default `units`). `retro` (v2.8 D3:
    ``{new_cards, to_review}``) feeds the overview's 「复盘卡 上次推送后新增 a · 待审定 b」 line; None keeps the
    overview without it. `fetch_status` (the collect node's ``[{family, status, ok_count, target_count}]``) feeds
    the overview's data line for the (at most three) families that fetched partially or failed (v2.8 §13.1)."""
    plans = []
    expiring = []
    tomorrow = add_trading_days(session, 1)
    for u in units:
        agent_field = _agent_field(u.get('agent'))
        plan = u.get('plan')
        if plan is None:
            # Rule plan skipped (e.g. ATR) but the agent plan is ok: the unit still
            # gets one entry, its rule-level fields empty (P3 §10.2).
            if agent_field is None or agent_field['status'] != 'ok':
                continue
            plan = ((u.get('agent') or {}).get('plan'))
        sizing = plan.get('sizing') or {}
        # P4 §7.5: an agent brief (agent-1, or agent-2's summary + first counter) already
        # sits in the agent line; it is not repeated after the plan. Other briefs still attach.
        brief = u.get('brief') or {}
        brief_hidden = brief.get('prompt_version') in AGENT_BRIEF_VERSIONS
        output = {} if brief_hidden else (brief.get('output') or {})
        snapshot_as_of = sizing.get('snapshot_as_of')
        context = (plan.get('inputs') or {}).get('context') or {}
        sector = context.get('sector') or None
        rule_plan = u.get('plan')
        plans.append({
            'ticker': u['ticker'], 'source': u.get('source'),
            'action': u['proposed_action'], 'rule_ids': u.get('rule_ids', []),
            'kind': plan['kind'], 'plan_version': plan.get('plan_version'), 'status': plan['status'],
            'entry_low': rule_plan['entry_low'] if rule_plan else None,
            'entry_high': rule_plan['entry_high'] if rule_plan else None,
            'stop': rule_plan['stop'] if rule_plan else None,
            'target1': rule_plan['target1'] if rule_plan else None,
            'shares': sizing.get('shares') if rule_plan else None,
            'risk_amount': sizing.get('risk_amount') if rule_plan else None,
            'sizing_status': sizing.get('status') if rule_plan else None,
            'snapshot_as_of': snapshot_as_of.isoformat() if snapshot_as_of else None,
            'valid_from': plan['valid_from'], 'valid_until': plan['valid_until'],
            'entry_type': rule_plan.get('entry_type') if rule_plan else None,
            'max_hold_sessions': rule_plan.get('max_hold_sessions') if rule_plan else None,
            'warnings': plan.get('warnings') or [], 'invalid_reasons': plan.get('invalid_reasons') or [],
            'regime_label': (context.get('regime') or {}).get('label'),
            'next_earnings': context.get('next_earnings').isoformat()
            if isinstance(context.get('next_earnings'), date) else context.get('next_earnings'),
            'sector': sector,
            'evidence': EVIDENCE_TEXT,
            'brief_summary': output.get('summary'),
            'contra': None if brief_hidden else ' | '.join(item.get('text', '') for item in (output.get('contra') or [])
                                 if isinstance(item, dict)),
            'url': f'{public_url.rstrip("/")}/today',
            'agent': agent_field,
            'merged': list(u.get('merged') or []),
        })
        valid_until = (agent_field or {}).get('valid_until') if agent_field and agent_field.get('entry_type') \
            else plan['valid_until']
        if valid_until is not None and valid_until <= tomorrow:
            expiring.append({'ticker': u['ticker'], 'valid_until': valid_until})

    def alerts(ticker: str) -> list[str]:
        candidates = (summary.get('tickers') or {}).get(ticker, {}).get('candidates') or []
        return [c for c in ('plan_stop', 'plan_target') if c in candidates]

    holdings_out = []
    if holding_status is not None:
        have = {row['ticker'] for row in holding_status}
        for row in holding_status:
            holdings_out.append({**row, 'earnings_eve': _earnings_eve(holdings, row['ticker'], tomorrow),
                                 'plan_alerts': alerts(row['ticker'])})
        for h in holdings:           # §13 / R18: no row for a held ticker (the review stage failed): old fields, marked
            if h['ticker'] not in have:
                holdings_out.append({**h, 'stale': True, 'earnings_eve': _earnings_eve(holdings, h['ticker'], tomorrow),
                                     'plan_alerts': alerts(h['ticker'])})
    else:
        for h in holdings:
            holdings_out.append({**h, 'earnings_eve': _earnings_eve(holdings, h['ticker'], tomorrow),
                                 'plan_alerts': alerts(h['ticker'])})

    errors = list(summary.get('errors') or [])
    errors += [f"{ticker} earnings: {status.split('error: ', 1)[1]}"
               for ticker, status in (summary.get('earnings') or {}).items()
               if isinstance(status, str) and status.startswith('error:')]

    errors_by_source = {source: list(items) for source, items in (summary.get('sources') or {}).items() if items}

    updates = select_plan_updates(plan_updates or [])
    # Migration 026 not run (plan_updates None): no plan-updates count in the overview at all.
    update_counts = (None if plan_updates is None else
                     {kind: sum(1 for u in updates if u['kind'] == kind) for kind in ('revise', 'invalidate')})
    exits = [h['ticker'] for h in holdings_out if h.get('instruction') == 'exit']
    pending = {'entry': [p['ticker'] for p in plans], 'holding': [h['ticker'] for h in holdings_out],
               'exit': exits, 'expiring': expiring}

    return {
        'session': session.isoformat(),
        'overview': {'session': session.isoformat(), 'new_plans': len(plans), 'holdings': len(holdings_out),
                     'pending': len(pending['entry']) + len(exits), 'errors': len(errors),
                     'last_push_failed': last_push_failed,
                     'extended_plans': extended_plans,
                     'core_empty': 'core_empty' in (summary.get('errors') or []),
                     'market': market,
                     'agent': _agent_counts(units if session_units is None else session_units, session, merged or set()),
                     'agent_quota_exhausted': bool((agent_summary or {}).get('quota_exhausted')),
                     **({'plan_updates': update_counts} if update_counts is not None else {}),
                     **({'retro': {'new_cards': int(retro.get('new_cards') or 0),
                                   'to_review': int(retro.get('to_review') or 0)}} if retro is not None else {}),
                     'fetch_degraded': _fetch_degraded(fetch_status)},
        'plans': plans,
        'plan_updates': updates,
        'holdings': holdings_out,
        'pending': pending,
        'errors': errors,
        'errors_by_source': errors_by_source,
        'screen': screen,
    }


def _earnings_eve(holdings: list[dict[str, Any]], ticker: str, tomorrow: date) -> bool:
    nxt = next((h.get('next_earnings') for h in holdings if h['ticker'] == ticker), None)
    return nxt is not None and nxt == tomorrow.isoformat()


def _fetch_degraded(fetch_status: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The families that fetched partially or failed, at most three, each with the number of targets that got nothing."""

    out = []
    for item in fetch_status or []:
        if item.get('status') not in ('partial', 'failed'):
            continue
        ok, total = int(item.get('ok_count') or 0), int(item.get('target_count') or 0)
        out.append({'family': item['family'], 'title': data_families.get(item['family']).title,
                    'ok_count': ok, 'target_count': total, 'missed': max(0, total - ok)})
    return out[:3]


def select_plan_updates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The window's reviews worth a push (v2.5 §10.3): invalidations, and revisions that produced a new
    version. Maintains, failed revisions, skips and errors stay out; so does an extended-pool chain still
    in its entry period (shadow-tracked only). A holding chain is always pushed."""
    out = []
    for row in rows:
        outcome = row.get('outcome')
        if outcome == 'revise' and row.get('new_plan_id') is None:
            continue
        if outcome not in ('revise', 'invalidate'):
            continue
        if row.get('mode') != 'holding' and row.get('pool') == 'extended':
            continue
        out.append({'ticker': row['ticker'], 'kind': outcome, 'from_version': row.get('from_version'),
                    'to_version': row.get('to_version') if outcome == 'revise' else None,
                    'changes': list(row.get('changes') or []) if outcome == 'revise' else [],
                    'levels': row.get('levels') if outcome == 'revise' else None,
                    'valid_until': row.get('valid_until') if outcome == 'revise' else None,
                    'decided_by': row.get('decided_by'), 'reason': row.get('reason'),
                    # v2.9 §11: a holding-phase invalidation is an exit instruction; a retrigger revision says so
                    'mode': row.get('mode'), 'via': row.get('via'), 'exit_reason': row.get('exit_reason'),
                    'retrigger': row.get('retrigger')})
    return out


def _agent_counts(units: list[dict[str, Any]], session: date, merged: set[tuple] | None = None) -> dict[str, int]:
    """Main-list units the agent stage covers (this session's conflict-free buys, §8.1) by
    outcome; anything but ok/pass counts as not given. A unit merged into an active chain (v2.9 §4.3) is no
    Agent suggestion owed: it counts as ``merged`` instead of ``missing``, and only shows up in the overview
    when there is one."""
    counts = {'ok': 0, 'pass': 0, 'missing': 0}
    merged = merged or set()
    merged_count = 0
    for u in units:
        if (u['signal_date'] != session or u.get('proposed_action') != 'buy'
                or 'conflict' in (u.get('labels') or [])):
            continue
        if (u['ticker'], u['signal_date']) in merged:
            merged_count += 1
            continue
        status = (u.get('agent') or {}).get('status')
        if status in counts:
            counts[status] += 1
        else:
            counts['missing'] += 1
    if merged_count:
        counts['merged'] = merged_count
    return counts
