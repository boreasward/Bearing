"""Read-only use cases behind the derivation endpoints (v2.4 §8–§9).

``load_rows`` reads exactly the rows a plan's ``inputs`` name, by id; every table involved is
append-only, so the row read back is the row the plan was generated from (a newer bar revision or a
corrected snapshot is a new row with a new id and is never picked up). The three use cases and the
batch check run inside ``service.read_only_tx()`` and never call an external data source: a row that
cannot be read back is reported as ``incomplete``, not fetched.

The derivation arithmetic itself lives in ``derivation`` (pure); nothing is recomputed here.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from investment_assistant.data import identity
from investment_assistant.workbench import derivation, repository, service
from investment_assistant.workbench import stages as stages_module
from investment_assistant.workbench.timeline import AGENT_V2, AGENT_V3

RUN_NOTE_PRE_V24 = 'v2.4 之前生成，无阶段证据'
RUN_NOTE_NO_TABLE = '迁移 025 未执行，暂无阶段证据'


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _by_id(conn, reader, row_id: Any, table: str, missing: list[str]) -> dict[str, Any] | None:
    """One row by id, or None; an id that is not a UUID or reads back nothing is listed as missing."""
    key = _uuid(row_id)
    row = reader(conn, key) if key is not None else None
    if row is None:
        missing.append(f'{table}:{row_id}')
    return row


def load_rows(conn, plan: dict[str, Any]) -> derivation.Rows:
    """The raw rows ``plan['inputs']`` point at, read back by id (numbers stay ``Decimal``)."""
    inp = plan.get('inputs') or {}
    missing: list[str] = []
    rows = derivation.Rows()

    bar_ids = list(inp.get('bar_ids') or [])
    if bar_ids:
        rows.bars = repository.bars_by_ids(conn, bar_ids)
        found = {b['id'] for b in rows.bars}
        missing += [f'price_bars:{i}' for i in bar_ids if int(i) not in found]
    if inp.get('earnings_date_id') is not None:
        rows.earnings = _by_id(conn, repository.earnings_row_by_id, inp['earnings_date_id'],
                               'earnings_dates', missing)
    if inp.get('snapshot_id') is not None:
        if (plan.get('sizing') or {}).get('equity_source') == 'ledger':
            raw_id = inp['snapshot_id']
            key = int(str(raw_id)) if str(raw_id).isdigit() else None
            frozen = (repository.equity_day_by_id(conn, key)
                      if key is not None and 0 < key < 2**63 and repository.accounts_ready(conn) else None)
            if frozen is None:
                missing.append(f'account_equity_days:{raw_id}')
            else:
                rows.snapshot = {**frozen, 'as_of': frozen['session'], 'equity_source': 'ledger'}
        else:
            rows.snapshot = _by_id(conn, repository.snapshot_by_id, inp['snapshot_id'],
                                   'account_snapshots', missing)
    regime = (inp.get('context') or {}).get('regime')
    if regime and regime.get('id') is not None and _uuid(regime['id']) is not None:
        rows.regime = repository.regime_by_id(conn, _uuid(regime['id']))   # display only, never a blocker
    if plan.get('kind') == 'own' and plan.get('based_on_id') is not None:
        rows.rule_plan = _by_id(conn, repository.plan_by_id, plan['based_on_id'], 'trade_plans', missing)
    if plan.get('kind') == 'agent' and inp.get('agent_run_id') is not None:
        rows.agent_run = _by_id(conn, repository.agent_run_by_id, inp['agent_run_id'], 'agent_runs', missing)
    if plan.get('kind') == 'agent' and plan.get('plan_version') in (AGENT_V2, AGENT_V3):
        # v2.5 §5.2: the version row; on a database without migration 026 it cannot exist either
        rows.version = (repository.version_by_plan(conn, plan['id'])
                        if plan.get('id') is not None and repository.timeline_ready(conn) else None)
        if rows.version is None:
            missing.append(f"agent_plan_versions:{plan.get('id')}")
        if plan.get('plan_version') == AGENT_V3 and repository.executable_ready(conn):
            # v2.9 D6: the entry-phase cap follows the chain's retrigger rows
            rows.retriggers = repository.chain_retriggers_for(conn, plan['ticker'],
                                                              chain_signal_date=plan['signal_date'])
    rows.candidates = repository.unit_candidates(conn, plan['ticker'], plan['signal_date'])
    rows.missing = missing
    return rows


def _run_evidence(conn, plan_id: str) -> dict[str, Any]:
    """Which workbench run inserted the plan (not the candidate's run: it may be older)."""
    if not repository.stage_runs_ready(conn):
        return {'ready': False, 'note': RUN_NOTE_NO_TABLE}
    row = repository.stage_run_for_plan(conn, plan_id)
    if row is None:
        return {'ready': True, 'run_id': None, 'note': RUN_NOTE_PRE_V24}
    return {'ready': True, 'run_id': row['run_id'], 'stage': row['stage'], 'status': row['status'],
            'derivation': (row['detail'] or {}).get('derivation')}


def _plan_id(plan_id: Any) -> str:
    key = _uuid(plan_id) if isinstance(plan_id, (str, uuid.UUID)) else None
    if key is None:
        raise ValueError(f'计划 id 不是合法的 UUID：{plan_id}')
    return str(key)


def plan_derivation(plan_id: str) -> dict[str, Any]:
    """``derive_plan`` of one stored plan plus ``trigger_note`` and the run evidence.

    ValueError for a non-UUID id, LookupError for an unknown plan."""
    plan_id = _plan_id(plan_id)
    with service.read_only_tx() as conn:
        plan = repository.plan_by_id(conn, plan_id)
        if plan is None:
            raise LookupError(f'计划不存在：{plan_id}')
        result = derivation.derive_plan(plan, load_rows(conn, plan))
        return {**result, 'trigger_note': derivation.TRIGGER_NOTE, 'run': _run_evidence(conn, plan_id)}


def unit_derivation(ticker: str, signal_date: date) -> dict[str, Any]:
    """The trigger section of one (ticker, signal_date) unit plus the ids of its plans.

    ValueError when ``ticker`` fails ``identity.stock_id``, LookupError when the unit has no candidates."""
    identity.stock_id(ticker)                       # ValueError for anything that is not a US ticker
    ticker = ticker.strip().upper()
    with service.read_only_tx() as conn:
        candidates = repository.unit_candidates(conn, ticker, signal_date)
        if not candidates:
            raise LookupError(f'该单元没有候选：{ticker} {signal_date}')
        plan_ids = [str(p['id']) for p in repository.plans_for_unit(conn, ticker, signal_date)]
    return {**derivation.derive_unit(candidates), 'plan_ids': plan_ids}


def stage_runs_view(*, session: date | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Stage rows as stored, grouped by run (first-seen order) and ordered by the stage registry.

    Exactly one of ``session`` / ``run_id``; ValueError otherwise."""
    if (session is None) == (run_id is None):
        raise ValueError('session 与 run_id 必须给出且只能给出一个')
    with service.read_only_tx() as conn:
        ready = repository.stage_runs_ready(conn)
        if not ready:
            stored: list[dict[str, Any]] = []
        elif session is not None:
            stored = repository.stage_runs_for_session(conn, session)
        else:
            stored = repository.stage_runs_for_run(conn, run_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in stored:
        grouped.setdefault(row['run_id'], []).append(row)
    def ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # each run in the stage order of its own version (stages-3 has ``retro`` before ``push``, stages-4 after it)
        ids = stages_module.STAGE_IDS_BY_VERSION.get(rows[0]['stages_version'], stages_module.STAGE_IDS)
        order = {stage: i for i, stage in enumerate(ids)}
        return sorted(rows, key=lambda r: order.get(r['stage'], len(order)))

    runs = [{'run_id': rid, 'rows': ordered(rows)} for rid, rows in grouped.items()]
    return {'stages': list(stages_module.STAGES), 'stage_runs_ready': ready, 'runs': runs}


def check_plans(plan_ids: list[str]) -> dict[str, Any]:
    """Derive every listed plan in ONE read-only transaction and count the verdicts.

    A plan that cannot be read is counted as ``incomplete`` (ticker None); a non-UUID id is a ValueError."""
    ids = [_plan_id(i) for i in plan_ids]
    items: list[dict[str, Any]] = []
    if ids:
        with service.read_only_tx() as conn:
            for plan_id in ids:
                plan = repository.plan_by_id(conn, plan_id)
                if plan is None:
                    items.append({'plan_id': plan_id, 'ticker': None, 'status': 'incomplete'})
                    continue
                verdict = derivation.derive_plan(plan, load_rows(conn, plan))['consistency']['status']
                items.append({'plan_id': plan_id, 'ticker': plan['ticker'], 'status': verdict})
    count = lambda status: sum(item['status'] == status for item in items)   # noqa: E731
    return {'checked': len(items), 'consistent': count('consistent'), 'mismatch': count('mismatch'),
            'incomplete': count('incomplete'), 'items': items}
