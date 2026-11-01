"""Pure functions behind the instrument page's new blocks (v2.6 spec §9.1–§9.2, D14, D16).

``focus`` writes the "what to watch today" lines, ``key_data`` is the Agent tools' own indicator snapshot
computed on request (nothing stored), ``context_view`` the background block (next earnings, regime, industry
ETF). No database access here: ``service.instrument`` reads the rows and hands them in.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.data.calendar import trading_days_between
from investment_assistant.core import texts as labels, schema as core_schema, tx as core_tx
from investment_assistant.core.constants import BENCHMARK
from investment_assistant.views import chart, reasoning
from investment_assistant.views.today import _holding_fields
from investment_assistant.execution import chains as execution_chains, plan_context as execution_plan_context
from investment_assistant.execution import repo as execution_repo, repo_legacy as execution_repo_legacy, timeline
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis.agent import graph as agent_graph
from typing import Callable
from datetime import timedelta
from investment_assistant.derivation.indicators import price

SH = ZoneInfo('Asia/Shanghai')
EARNINGS_WITHIN_SESSIONS = 10
NONE_TEXT = '今天没有触发，也没有进行中的计划或持仓'
CONFLICT_NOTE = '· 同日有买有卖，默认观察'
OUTCOME_TEXT = {'maintain': '维持', 'revise': '修订', 'invalidate': '作废', 'skipped': '跳过', 'error': '出错'}
DECIDED_BY_TEXT = {'agent': 'Agent', 'host': '系统规则'}
MODE_TEXT = {'entry': '入场期', 'holding': '持仓期'}


def _money(value: Any) -> str:
    try:
        return f'{Decimal(str(value)):.2f}'
    except Exception:    # noqa: BLE001 - display only
        return str(value)


def _quantity(value: Any) -> str:
    text = format(Decimal(str(value)), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _percent(ratio: float) -> str:
    return f'{ratio * 100:.1f}%'


def _beijing(moment: datetime | None) -> str:
    return moment.astimezone(SH).strftime('%Y-%m-%d %H:%M') if moment is not None else '-'


def sessions_until(day: date, session: date) -> int:
    """Trading days in (session, day]; 0 for the session itself and for any earlier day."""
    return len(trading_days_between(session + timedelta(days=1), day))


def _position_item(position: dict) -> dict:
    stop = position.get('stop')
    if stop is None:
        stop_text = '当前止损 无（无计划，按回撤规则）'
    else:
        basis = '按你决定的计划' if position.get('plan_id') else '无计划，按回撤规则'
        stop_text = f'当前止损 {_money(stop)}（{basis}）'
        if position.get('to_stop_pct') is not None:
            stop_text += f"· 距止损 {_percent(position['to_stop_pct'])}"
    text = f"持仓 {_quantity(position['quantity'])} 股 · 成本 {_money(position['avg_cost'])} · {stop_text}"
    return {'kind': 'position', 'text': text, 'link': '#review/trades', 'concepts': ['review.position', 'plan.stop']}


def _chain_item(chain: dict) -> dict:
    review = chain.get('latest_review')
    if review:
        last = (f"最近复查：{OUTCOME_TEXT.get(review['outcome'], review['outcome'])}"
                f"（{DECIDED_BY_TEXT.get(review.get('decided_by'), review.get('decided_by') or '-')}，"
                f"{_beijing(review.get('created_at'))}）")
    else:
        last = '尚未复查'
    text = (f"Agent 第 {chain['version_no']} 版 · {MODE_TEXT.get(chain.get('mode'), chain.get('mode') or '-')} · "
            f"入场 {_money(chain['entry_low'])}–{_money(chain['entry_high'])} · 止损 {_money(chain['stop'])} · "
            f"T1 {_money(chain['target1'])} · {last}")
    return {'kind': 'agent_plan', 'text': text, 'link': f"#plan/{chain['head_plan_id']}",
            'concepts': ['agent.version_chain', 'review.chain_state']}


def _trigger_item(candidates: list[dict]) -> dict:
    named = '、'.join(f"{labels.RULE_TEXT.get(c['rule_id'], c['rule_id'])}（{labels.ACTION_TEXT.get(c['action'], c['action'])}）"
                      for c in candidates)
    actions = {c['action'] for c in candidates}
    text = f'今天触发 {named}' + (CONFLICT_NOTE if {'buy', 'sell'} <= actions else '')
    rules = [f"rule.{c['rule_id']}" for c in candidates if c['rule_id'] in labels.RULE_TEXT]
    return {'kind': 'trigger', 'text': text, 'link': '#today',
            'concepts': ['system.decision_unit', *dict.fromkeys(rules)]}


def _earnings_item(earnings: dict | None, session: date) -> dict | None:
    if not earnings or earnings.get('status') != 'known' or earnings.get('earnings_date') is None:
        return None
    day = earnings['earnings_date']
    if day < session:
        return None
    count = sessions_until(day, session)
    if count > EARNINGS_WITHIN_SESSIONS:
        return None
    text = f'今天发布财报（{day.isoformat()}）' if count == 0 else f'{count} 个交易日后发布财报（{day.isoformat()}）'
    return {'kind': 'earnings', 'text': text, 'link': None,
            'concepts': ['plan.earnings']}


def focus(*, position: dict | None, chains: list[dict], candidates_today: list[dict], earnings: dict | None,
          session: date) -> list[dict]:
    """"今天该关注什么" (spec §9.2): position, running agent plans, today's triggers, near earnings; else one line.

    ``position``: ``{quantity, avg_cost, stop, plan_id, to_stop_pct}`` (stop / plan_id None without a plan);
    ``chains``: entry / holding chains ``{version_no, mode, head_plan_id, entry_low, entry_high, stop, target1,
    latest_review}``; ``candidates_today``: the candidates of the latest closed session;
    ``earnings``: the latest ``earnings_dates`` row."""
    items: list[dict] = []
    if position is not None:
        items.append(_position_item(position))
    items.extend(_chain_item(chain) for chain in chains)
    if candidates_today:
        items.append(_trigger_item(candidates_today))
    soon = _earnings_item(earnings, session)
    if soon is not None:
        items.append(soon)
    return items or [{'kind': 'none', 'text': NONE_TEXT, 'link': None, 'concepts': []}]


def key_data(bars: list[dict], spy_bars: list[dict], *, as_of: date) -> dict:
    """The Agent tools' indicator snapshot at the latest close (D14) with the concept of every value."""
    return {'as_of': as_of.isoformat(), 'values': price.indicators(bars, spy_bars),
            'concepts': dict(reasoning.TOOL_CONCEPTS['get_indicators']['fields'])}


def context_view(*, earnings: dict | None, regime: dict | None, sector_etf: str | None, session: date) -> dict:
    """The background block: ``{earnings, regime, sector}``, each None when there is nothing to show."""
    earnings_view = None
    if earnings is not None:
        day = earnings.get('earnings_date')
        known = earnings.get('status') == 'known' and day is not None
        earnings_view = {'date': day.isoformat() if day else None, 'status': earnings.get('status'),
                         'sessions_until': sessions_until(day, session) if known and day >= session else None}
    regime_view = None
    if regime is not None:
        regime_view = {'label': regime.get('label'), 'session': regime['session'].isoformat()}
    sector_view = None
    if sector_etf:
        snap = (regime or {}).get('sectors', {}).get(sector_etf) or {}
        sector_view = {'etf': sector_etf, 'state': snap.get('state'), 'rs20': snap.get('rs20'),
                       'rs60': snap.get('rs60')}
    return {'earnings': earnings_view, 'regime': regime_view, 'sector': sector_view}


def _instrument_holding(conn, ticker: str, book: dict[str, Any], ticker_bars: list[dict[str, Any]], session: date,
                        *, now: datetime) -> dict[str, Any] | None:
    """The 「今日」 holdings row of this ticker (same bars, same ``execution_chains._position_plan_state``); None when not held."""
    if book['position'] is None:
        return None
    plan, state = execution_chains._position_plan_state(conn, ticker, book, ticker_bars, session, now=now)
    return _holding_fields(book, ticker_bars, plan, state)

def _instrument_chains(conn, ticker: str, book: dict[str, Any], session: date, *,
                       now: datetime) -> list[dict[str, Any]]:
    """The ticker's agent chains in state entry / holding / exiting at S (`timeline-2`), newest signal date first
    (empty without 026)."""
    if not core_schema.timeline_ready(conn):
        return []
    rows = []
    for chain in execution_repo.all_chains(conn):
        if chain['ticker'] != ticker:
            continue
        status = execution_chains.chain_status(conn, ticker, chain['signal_date'], session=session, now=now)
        if status is None or status['state']['state'] not in timeline.ACTIVE_STATES:
            continue
        plan = status['head']['plan']
        rows.append({'signal_date': chain['signal_date'], 'state': status['state']['state'],
                     'mode': status['state']['mode'], 'version_no': status['head']['version']['version_no'],
                     'head_plan_id': str(plan['id']), 'entry_low': plan['entry_low'], 'entry_high': plan['entry_high'],
                     'stop': plan['stop'], 'target1': plan['target1'], 'latest_review': status['latest_review']})
    rows.sort(key=lambda r: r['signal_date'], reverse=True)
    return rows

def _instrument_plans(conn, ticker: str, since: date) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(plan rows of the page, str(plan_id) -> version row): every plan whose validity reaches the chart window.
    Version rows exist for agent-2 plans once migration 026 has run; without them ``version_no`` is None."""
    rows = execution_repo.plans_for_ticker(conn, ticker, since=since)
    ready = core_schema.timeline_ready(conn)
    versions: dict[str, dict[str, Any]] = {}
    if ready:
        for plan in rows:
            if plan['kind'] == 'agent' and plan['plan_version'] in (agent_graph.AGENT_V2, agent_graph.AGENT_V3):
                version = execution_repo.version_by_plan(conn, plan['id'])
                if version is not None:
                    versions[str(plan['id'])] = version
    return rows, versions

def instrument(ticker: str, *, now: datetime) -> dict[str, Any]:
    """The instrument page payload. The original keys are read as before; v2.6 adds ``focus``, ``key_data``,
    ``context``, ``plans``, ``holding`` and ``chart.annotations`` (spec §9.4; ``holding`` is the 「今日」 holdings row
    of this ticker or None), each in its own savepoint: a failing block is
    ``None`` and its reason lands in ``errors`` (keyed by block), the rest of the page is unaffected."""
    ticker = ticker.strip().upper()
    session = bars.latest_closed_session(now)
    errors: dict[str, str] = {}
    with core_tx.read_only_tx() as conn:
        def guarded(key: str, fn: Callable[[], Any]) -> Any:
            try:
                with conn.transaction():          # a savepoint: one failed statement must not poison the page
                    return fn()
            except Exception as exc:              # noqa: BLE001 - isolate the block, report why
                errors[key] = str(exc) or type(exc).__name__
                return None

        book = execution_plan_context._book(conn, ticker)
        chart_bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=chart.READ_LIMIT)
        chart_payload = chart.build(chart_bars)
        shown = chart_payload['bars']
        first_day = shown[0]['price_date'] if shown else session - timedelta(days=120)
        candidates = data_repo.read_candidates(conn, ticker, since=first_day)
        trades = execution_repo.list_trades(conn, ticker)
        earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=now)

        plan_data = guarded('plans', lambda: _instrument_plans(conn, ticker, first_day))
        plan_rows, versions = plan_data if plan_data is not None else ([], {})
        plans_view = None if plan_data is None else [
            {'id': str(p['id']), 'kind': p['kind'], 'plan_version': p['plan_version'],
             'signal_date': p['signal_date'], 'entry_low': p['entry_low'], 'entry_high': p['entry_high'],
             'stop': p['stop'], 'target1': p['target1'], 'valid_from': p['valid_from'],
             'valid_until': p['valid_until'], 'status': p['status'],
             'version_no': versions[str(p['id'])]['version_no'] if str(p['id']) in versions else None}
            for p in plan_rows]
        annotations = guarded('chart.annotations', lambda: chart.annotate(
            bars=shown, candidates=candidates, plans=plan_rows, versions=versions, trades=trades))
        # the bars 「今日」 holdings and get_indicators read (default window), shared by holding and key_data
        ticker_bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now)
        holding = guarded('holding', lambda: _instrument_holding(conn, ticker, book, ticker_bars, session, now=now))

        def focus_view():
            if book['position'] is not None and holding is None:
                raise RuntimeError('持仓止损读取失败，见 errors.holding')
            return focus(
                position=holding, chains=_instrument_chains(conn, ticker, book, session, now=now),
                candidates_today=[c for c in candidates if c['signal_date'] == session],
                earnings=earnings, session=session)

        focus_result = guarded('focus', focus_view)

        def read_key_data():
            spy = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now)
            as_of = ticker_bars[-1]['price_date'] if ticker_bars else session
            return key_data(ticker_bars, spy, as_of=as_of)

        def context():
            entry = data_repo.pool_entry_at(conn, ticker, session, cutoff_at=now)
            return context_view(
                earnings=earnings, regime=analysis_repo.latest_regime(conn, on_or_before=session, cutoff_at=now),
                sector_etf=entry.get('sector_etf') if entry else None, session=session)

        key_data_view = guarded('key_data', read_key_data)
        context_result = guarded('context', context)

        def my_knowledge_block():
            from investment_assistant.review.knowledge import my_knowledge

            return my_knowledge.for_ticker(conn, ticker)

        knowledge_view = guarded('knowledge', my_knowledge_block)      # v2.7 §5.5: the 「我的知识」 block
        return {
            'ticker': ticker, 'session': session,
            'bars': data_repo.read_bars(conn, ticker, session, cutoff_at=now, limit=120),
            'chart': {**chart_payload, 'annotations': annotations},
            'filings': data_repo.read_filings(conn, ticker, since=session - timedelta(days=180), cutoff_at=now),
            'news': data_repo.read_news(conn, ticker, cutoff_at=now, since=execution_plan_context._midnight(session - timedelta(days=60))),
            'candidates': candidates,
            'decisions': execution_repo_legacy.decision_heads(conn, ticker=ticker),
            'trades': trades,
            'position': book['position'], 'closed': book['closed'],
            'focus': focus_result, 'key_data': key_data_view, 'context': context_result, 'plans': plans_view,
            'holding': holding, 'knowledge': knowledge_view, 'errors': errors,
        }
