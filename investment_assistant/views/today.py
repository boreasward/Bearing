from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from investment_assistant.core import features as core_features, schema as core_schema, tx as core_tx
from investment_assistant.core.constants import OPEN_UNIT_DAYS, NEWS_LOOKBACK_DAYS, REAL_ACCOUNT_ID
from investment_assistant.core.texts import RUN_REASON_TEXT
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.execution import chains as execution_chains, plan_context as execution_plan_context, repo as execution_repo
from investment_assistant.analysis import repo as analysis_repo, screening, synthesis, units as analysis_units
from investment_assistant.analysis.agent import graph as agent_graph

def _holding_fields(book: dict[str, Any], bars_: list[dict[str, Any]], plan: dict[str, Any] | None,
                    state: dict[str, Any] | None) -> dict[str, Any]:
    """The holding row shared by 「今日」 and the instrument page: position, current stop and T1 from the
    plan the user decided on (``execution_chains._position_plan_state``), distance of the last close to both."""
    position = book['position']
    last_close = bars_[-1]['close'] if bars_ else None
    return {
        'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
        'plan_id': plan['id'] if plan else None,
        'stop': state['stop'] if state else None, 'mode': state['mode'] if state else None,
        't1_reached': state['t1_reached'] if state else False, 'target1': state['target1'] if state else None,
        'last_close': last_close,
        'to_stop_pct': float(last_close / float(state['stop']) - 1) if state and last_close else None,
        'to_target_pct': float(float(state['target1']) / last_close - 1) if state and last_close else None,
    }

def _holdings(conn, session: date, *, now: datetime) -> list[dict[str, Any]]:
    out = []
    for ticker in sorted({t['ticker'] for t in execution_repo.list_trades(conn) if t['voided_at'] is None}):
        book = execution_plan_context._book(conn, ticker)
        position = book['position']
        if position is None:
            continue
        bars_ = data_repo.read_bars(conn, ticker, session, cutoff_at=now)
        plan, state = execution_chains._position_plan_state(conn, ticker, book, bars_, session, now=now)
        earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=now)
        next_earnings = (earnings['earnings_date'] if earnings and earnings['status'] == 'known'
                         and earnings['earnings_date'] and earnings['earnings_date'] >= session else None)
        out.append({'ticker': ticker, **_holding_fields(book, bars_, plan, state),
                    'next_earnings': next_earnings.isoformat() if next_earnings else None})
    return out

def _brief_view(brief: dict[str, Any] | None) -> dict[str, Any] | None:
    if brief is None or brief['status'] != 'error':
        return brief
    # A brief citing unknown sources is never shown; only its failure is.
    return {k: brief[k] for k in ('id', 'status', 'error', 'created_at', 'prompt_version')}

def _attach_knowledge_refs(conn, agents: list[dict[str, Any] | None]) -> None:
    """v2.7 §6.2: every ok / pass agent block (the ones that show a plan) gets ``knowledge_refs`` — the knowledge
    versions its (head) plan cited, an empty list without citations or without migration 027. Blocks that only
    carry a status and a reason (error, skipped, rejected) keep their small shape."""
    from investment_assistant.review.knowledge import my_knowledge

    blocks = [a for a in agents if a is not None and 'plan' in a]
    refs = data_repo.plan_knowledge_refs(conn, [(a['plan'] or {}).get('id') for a in blocks])
    for block in blocks:
        plan_id = (block['plan'] or {}).get('id')
        block['knowledge_refs'] = refs.get(str(plan_id), []) if plan_id is not None else []

def _unit_cards(conn, session: date, *, now: datetime, ready: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The undecided units of the last ``OPEN_UNIT_DAYS`` days as unit cards: ``(main-list units, extended-pool
    units)``. Since v2.9 「今日」 no longer shows them (the plan table does, D34); the push and the read helpers
    below still build their entries from them."""
    units: list[dict[str, Any]] = []
    extended_units: list[dict[str, Any]] = []
    for group in analysis_repo.open_candidate_groups(conn, since=session - timedelta(days=OPEN_UNIT_DAYS)):
        ticker, signal_date = group['ticker'], group['signal_date']
        book = execution_plan_context._book(conn, ticker)
        held = book['position'] is not None
        unit = synthesis.synthesize(ticker, signal_date, group['candidates'], holding=held, now=now)
        since = signal_date - timedelta(days=NEWS_LOOKBACK_DAYS)
        entry = data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
        unit.update(
            source=entry.get('source') if entry else None,
            candidates=group['candidates'], position=book['position'],
            brief=_brief_view(analysis_repo.latest_brief(conn, ticker, signal_date)),
            plan=execution_repo.rule_plan(conn, ticker, signal_date),
            filings=data_repo.read_filings(conn, ticker, since=since, cutoff_at=now),
            news=data_repo.read_news(conn, ticker, cutoff_at=now, since=execution_plan_context._midnight(since), limit=20),
            agent=_unit_agent(conn, ticker, signal_date, session=session, ready=ready, book=book, now=now))
        # The tier as of the signal date decides the list, not today's tier.
        if analysis_units._unit_is_extended(conn, ticker, signal_date, held=held, now=now):
            agent_run = analysis_repo.latest_agent_run(conn, ticker, signal_date)
            extended_units.append({'ticker': ticker, 'signal_date': signal_date,
                                   'proposed_action': unit['proposed_action'],
                                   'rule_ids': unit['rule_ids'], 'plan': unit['plan'],
                                   'agent_status': _agent_status_line(agent_run)})
        else:
            units.append(unit)
    _attach_knowledge_refs(conn, [unit.get('agent') for unit in units])
    return units, extended_units

PORTFOLIO_FLAG = 'portfolio_manager'
_PORTFOLIO_REASONS = {'migration_031_missing': '需执行迁移 031', 'feature_disabled': RUN_REASON_TEXT['feature_disabled'],
                      'not_run': '组合检查节点尚未运行', 'agent_unavailable': RUN_REASON_TEXT['agent_unavailable']}


def portfolio_check_view(conn, session: date, *, account_id=REAL_ACCOUNT_ID, enabled: bool | None = None) -> dict[str, Any]:
    """The account's portfolio check of ``session`` as 「今日」 shows it (spec §7.4, K2):
    ``{status, reason, session, cutoff_at, result, commentary}``.

    ``not_computed``: migration 031 has not run; the ``portfolio_manager`` switch is off (the whole node was skipped, so
    a stored row of the same session from an earlier run is deliberately NOT shown as this version's check); or the
    switch is on but the node left no row (it has not run yet, or failed). ``degraded``: the hard result exists but has
    no successful PM commentary (model unavailable, rejected or failed). ``ready``: hard result and commentary.
    ``enabled`` is the switch the caller reads (the current one by default; the push passes the run's frozen one)."""
    view: dict[str, Any] = {'status': 'not_computed', 'reason': None, 'session': session, 'cutoff_at': None,
                            'result': None, 'commentary': None}
    if not core_schema.five_layers_ready(conn):
        return {**view, 'reason': _PORTFOLIO_REASONS['migration_031_missing']}
    if enabled is None:
        enabled = core_features.current(conn)[PORTFOLIO_FLAG]
    if not enabled:
        return {**view, 'reason': _PORTFOLIO_REASONS['feature_disabled']}
    row = execution_repo.portfolio_check_for(conn, account_id, session)
    if row is None:
        return {**view, 'reason': _PORTFOLIO_REASONS['not_run']}
    view.update(cutoff_at=row['cutoff_at'], result=row['result'], commentary=row['commentary'])
    if row['commentary'] is None:
        return {**view, 'status': 'degraded', 'reason': _PORTFOLIO_REASONS['agent_unavailable']}
    return {**view, 'status': 'ready'}


def _today_view(conn, session: date, *, now: datetime, cards: bool) -> dict[str, Any]:
    ready = core_schema.timeline_ready(conn)      # once per request (v2.5 §6.5)
    units, extended_units = _unit_cards(conn, session, now=now, ready=ready) if cards else ([], [])
    holdings = _holdings(conn, session, now=now)
    chains = execution_chains._active_chains(conn, session, now=now) if ready else []
    held = execution_chains.held_chain_states(conn, session, now=now) if ready else {}
    for holding in holdings:      # D7: a holding without an agent-2 chain is not reviewed
        holding['has_chain'] = holding['ticker'] in held
        holding['chain_state'] = held.get(holding['ticker'])
    market = analysis_repo.latest_regime(conn, on_or_before=session, cutoff_at=now)
    screen_report = screening.report(conn, session, now=now)
    view = {'session': session, 'units': units, 'extended_units': extended_units,
            'holdings': holdings, 'market': market, 'screen': screen_report,
            'chains': chains, 'timeline_ready': ready,
            'portfolio_check': portfolio_check_view(conn, session)}
    if not cards:
        view['retired'] = True
    return view

def today(*, now: datetime) -> dict[str, Any]:
    """The 「今日」 payload. v2.9 D34 retires the unit cards: ``units`` and ``extended_units`` stay as empty lists with
    ``retired: true`` (the page reads ``GET /plan-table`` instead); the holdings, the active chains, the market and the
    screen report are as before, chains judged by `timeline-2`."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        return _today_view(conn, session, now=now, cards=False)

def unit_cards(*, now: datetime) -> dict[str, Any]:
    """The old 「今日」 payload with its unit cards (the undecided units of the last 14 days). Not an endpoint any more;
    the push and the tests read the units through it."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        return _today_view(conn, session, now=now, cards=True)

def _agent_view(run: dict[str, Any] | None, plan: dict[str, Any] | None,
                chain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One unit's agent block (§10.1; v2.5 §10.1): full suggestion on ok/pass, status and reason otherwise.

    For an agent-2 chain ``run`` / ``plan`` are the head version's run and plan (not the latest run) and
    ``chain`` is ``execution_chains._chain_view``'s dict; an ok/pass view always carries ``chain`` (None without a chain).
    An agent-1 ok / pass run is marked ``legacy: True`` (D2: the unit has an agent-1 suggestion, read-only,
    never reviewed); an agent-1 run that was skipped / failed left no plan and takes the「Agent 未给出计划」
    path (§10.1)."""
    if run is None:
        return None
    if run['status'] in ('ok', 'pass'):
        view = {'id': str(run['id']), 'status': run['status'], 'model': run['model'],
                'agent_version': run['agent_version'], 'output': run['output'], 'counter': run['counter'],
                'plan': plan, 'input_tokens': run['input_tokens'], 'output_tokens': run['output_tokens'],
                'created_at': run['created_at'], 'chain': chain}
    else:
        view = {'id': str(run['id']), 'status': run['status'], 'reason': run['reason'],
                'created_at': run['created_at']}
    if run.get('agent_version') == agent_graph.AGENT_VERSION and run['status'] in ('ok', 'pass'):
        view['legacy'] = True
    return view

def _unit_agent(conn, ticker: str, signal_date: date, *, session: date, ready: bool,
                book: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    """The unit's agent block: the chain head's run and plan when there is an agent-2 chain, else the latest
    propose run and the unit's agent plan as before (agent-1 units, units without an ok run, migration 026
    missing). With ``now`` the chain is judged by `timeline-2`."""
    status = execution_chains.chain_status(conn, ticker, signal_date, session=session, book=book, now=now) if ready else None
    if status is None:
        return _agent_view(analysis_repo.latest_agent_run(conn, ticker, signal_date),
                           execution_repo.agent_plan(conn, ticker, signal_date))
    run = data_repo.agent_run_by_id(conn, status['head']['version']['agent_run_id'])
    return _agent_view(run, status['head']['plan'], execution_chains._chain_view(status))

def _agent_status_line(run: dict[str, Any] | None) -> str | None:
    if run is None:
        return None
    return run['status'] if run['status'] in ('ok', 'pass') else f"{run['status']}: {run['reason']}"
