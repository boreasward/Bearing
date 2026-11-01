"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from psycopg.rows import dict_row
from investment_assistant.core.constants import REAL_ACCOUNT_ID
from investment_assistant.core.dbutil import _one, _all, _plan_json, _account_scope, _account_columns_ready

from investment_assistant.core.idempotency import Conflict
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import positions

_TRADE_COLUMNS = ('id, request_id, request_hash, ticker, side, quantity, price, fee, executed_at, decision_id, '
                  'plan_id, note, recorded_at, voided_at, void_reason, account_id, recorded_by, detail')


def _trade_columns(accounts_enabled: bool) -> str:
    return _TRADE_COLUMNS if accounts_enabled else _TRADE_COLUMNS.removesuffix(', account_id, recorded_by, detail')


def trade_by_request(conn, request_id, *, account_id=REAL_ACCOUNT_ID):
    ready, scope, params = _account_scope(conn, account_id)
    return _one(conn, f'SELECT {_trade_columns(ready)} FROM trades WHERE request_id = %s{scope}',
                [request_id, *params])


def trade_by_id(conn, trade_id, *, account_id=REAL_ACCOUNT_ID):
    ready, scope, params = _account_scope(conn, account_id)
    return _one(conn, f'SELECT {_trade_columns(ready)} FROM trades WHERE id = %s{scope}', [trade_id, *params])


def _trades_where(conn, clause: str | None, params: list[Any], cutoff: datetime | None,
                  account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Trades of one account (`account_id=None` reads every account's), oldest first."""
    clauses = [clause] if clause else []
    params = list(params)
    ready, scope, account_params = _account_scope(conn, account_id, allow_all=True)
    if scope:
        clauses.append(scope.removeprefix(' AND '))
        params.extend(account_params)
    columns, head = _trade_columns(ready), []
    if cutoff is not None:
        clauses.append('recorded_at <= %s AND executed_at <= %s')
        params.extend([cutoff, cutoff])
        # a void made after the cutoff is not known yet (compared in SQL, so a naive cutoff behaves like the rest)
        columns = columns.replace(
            'voided_at, void_reason',
            'CASE WHEN voided_at <= %s THEN voided_at END AS voided_at, '
            'CASE WHEN voided_at <= %s THEN void_reason END AS void_reason')
        head = [cutoff, cutoff]
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    return _all(conn, f'SELECT {columns} FROM trades{where} ORDER BY executed_at, id', head + params)


def list_trades(conn, ticker: str | None = None, *, cutoff: datetime | None = None,
                account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Trades oldest first, by default the real account's only (spec D4: every pre-030 reader keeps that view;
    `account_id=None` reads all accounts). With `cutoff` only what was both executed and recorded by then (a later
    back-fill of an earlier trade is invisible), and a void recorded after `cutoff` is not applied yet (v2.9 D33)."""
    if ticker is None:
        return _trades_where(conn, None, [], cutoff, account_id)
    return _trades_where(conn, 'ticker = %s', [ticker.upper()], cutoff, account_id)


def insert_trade(conn, rec: dict[str, Any], digest: str, *, recorded_at: datetime) -> dict[str, Any]:
    """`plan_id` (migration 029) is the plan version the trade followed; it and the legacy `decision_id` never both
    carry a value (`trades_link_one_check`). Both default to None. `account_id` (migration 030) defaults to the real
    account, `recorded_by` marks host-written paper fills, `detail` carries the fill case / opening-position flag."""
    account_id = rec.get('account_id') or REAL_ACCOUNT_ID
    ready, _, _ = _account_scope(conn, account_id)
    extra_columns = ', account_id, recorded_by, detail' if ready else ''
    extra_values = ', %s, %s, %s::jsonb' if ready else ''
    extra_params = [account_id, rec.get('recorded_by', 'user'), _plan_json(rec.get('detail') or {})] if ready else []
    return _one(conn,
                'INSERT INTO trades (request_id, request_hash, ticker, side, quantity, price, fee, executed_at, '
                f'decision_id, plan_id, note, recorded_at{extra_columns}) '
                f'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{extra_values}) '
                f'RETURNING {_trade_columns(ready)}',
                [rec['request_id'], digest, rec['ticker'], rec['side'], rec['quantity'], rec['price'], rec['fee'],
                 rec['executed_at'], rec.get('decision_id'), rec.get('plan_id'), rec['note'], recorded_at,
                 *extra_params])


def trades_by_plan_ids(conn, plan_ids, *, cutoff: datetime | None = None,
                       account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Trades (voided ones included; the caller filters) linked to any of the plan versions, oldest first.
    `cutoff` and `account_id` as in `list_trades`."""
    ids = list(plan_ids)
    if not ids:
        return []
    return _trades_where(conn, 'plan_id = ANY(%s)', [ids], cutoff, account_id)


def mark_trade_voided(conn, trade_id, reason: str, *, voided_at: datetime) -> dict[str, Any]:
    # The only post-insert write in the workbench: status fields, never the trade itself.
    columns = _trade_columns(_account_columns_ready(conn))
    return _one(conn, f'UPDATE trades SET voided_at = %s, void_reason = %s WHERE id = %s AND voided_at IS NULL '
                      f'RETURNING {columns}', (voided_at, reason, trade_id))


_PLAN_COLUMNS = ('id, ticker, signal_date, kind, based_on_id, plan_version, ref_price, atr, entry_low, '
                 'entry_high, stop, target1, trail_atr_multiple, valid_from, valid_until, sizing, status, '
                 'invalid_reasons, warnings, inputs, request_id, request_hash, created_at, entry_type, '
                 'max_hold_sessions')


def store_plan(conn, plan: dict[str, Any]) -> dict[str, Any]:
    """Insert once per rule unit; same levels/sizing/status returns the stored row, different is a conflict."""
    if plan.get('kind') == 'rule':
        existing = rule_plan(conn, plan['ticker'], plan['signal_date'], plan.get('plan_version', 'plan-1'))
        if existing is not None:
            # Before v2.11 a missing source meant snapshot. Normalize only the
            # comparison: frozen rows and all actual sizing inputs stay intact.
            existing_sizing = {'equity_source': 'snapshot', **existing['sizing']}
            proposed_sizing = {'equity_source': 'snapshot', **plan['sizing']}
            same = (existing['status'] == plan['status']
                    and all(Decimal(existing[k]) == plan[k]
                            for k in ('ref_price', 'atr', 'entry_low', 'entry_high', 'stop', 'target1'))
                    and _plan_json(existing_sizing) == _plan_json(proposed_sizing)
                    and existing['entry_type'] == plan.get('entry_type')
                    and existing['max_hold_sessions'] == plan.get('max_hold_sessions'))
            if same:
                return {'status': 'existing', 'record': existing}
            return {'status': 'conflict', 'record': existing}
    return {'status': 'inserted', 'record': insert_plan(conn, plan)}


def insert_plan(conn, plan: dict[str, Any]) -> dict[str, Any]:
    return _one(conn,
                'INSERT INTO trade_plans (ticker, signal_date, kind, based_on_id, plan_version, ref_price, atr, '
                'entry_low, entry_high, stop, target1, trail_atr_multiple, valid_from, valid_until, sizing, '
                'status, invalid_reasons, warnings, inputs, request_id, request_hash, entry_type, '
                'max_hold_sessions) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, '
                f'%s, %s, %s, %s) RETURNING {_PLAN_COLUMNS}',
                (plan['ticker'], plan['signal_date'], plan['kind'], plan.get('based_on_id'),
                 plan.get('plan_version', 'plan-1'), plan['ref_price'], plan['atr'], plan['entry_low'],
                 plan['entry_high'], plan['stop'], plan['target1'], plan['trail_atr_multiple'],
                 plan['valid_from'], plan['valid_until'], _plan_json(plan['sizing']), plan['status'],
                 plan['invalid_reasons'], plan['warnings'], _plan_json(plan['inputs']),
                 plan.get('request_id'), plan.get('request_hash'), plan.get('entry_type'),
                 plan.get('max_hold_sessions')))


def plan_for_buy_trades(conn, trade_ids: list, *, account_id=REAL_ACCOUNT_ID) -> dict[Any, dict[str, Any]]:
    """Trade id -> the plan its decision confirmed, for trades that have one (default the real account's)."""
    if not trade_ids:
        return {}
    _, scope, params = _account_scope(conn, account_id, alias='t')
    plan_columns = ', '.join('p.' + c.strip() for c in _PLAN_COLUMNS.split(','))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f'SELECT t.id AS trade_id, {plan_columns} FROM trades t '
                    'JOIN signal_decisions d ON d.id = t.decision_id '
                    f'JOIN trade_plans p ON p.id = d.plan_id WHERE t.id = ANY(%s){scope}',
                    [list(trade_ids), *params])
        return {row['trade_id']: {k: row[k] for k in row if k != 'trade_id'} for row in cur.fetchall()}


def plan_by_request(conn, request_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE request_id = %s', (request_id,))


def rule_plan(conn, ticker: str, signal_date: date, plan_version: str | None = None) -> dict[str, Any] | None:
    """The unit's rule plan: the newest of any version (`plan_version=None`, v2.9 R7), or of the given one."""
    version_clause = '' if plan_version is None else 'AND plan_version = %s '
    params = (ticker.upper(), signal_date) + (() if plan_version is None else (plan_version,))
    return _one(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE ticker = %s AND signal_date = %s '
                      f"AND kind = 'rule' {version_clause}ORDER BY created_at DESC, plan_version DESC, id DESC LIMIT 1", params)


def plan_by_id(conn, plan_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE id = %s', (plan_id,))


def plans_for_unit(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """Every plan of one decision unit (rule, own, agent), oldest first."""
    return _all(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE ticker = %s AND signal_date = %s '
                      'ORDER BY created_at, id', (ticker.upper(), signal_date))


def plans_since(conn, since: date) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE signal_date >= %s '
                      'ORDER BY signal_date, created_at, id', (since,))


def plans_for_ticker(conn, ticker: str, *, since: date) -> list[dict[str, Any]]:
    """Every plan of one ticker (rule, own, agent) whose validity runs to ``since`` or later, oldest first."""
    return _all(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE ticker = %s AND valid_until >= %s '
                      'ORDER BY signal_date, created_at, id', (ticker.upper(), since))


_SNAPSHOT_COLUMNS = ('id, request_id, request_hash, as_of, equity, note, recorded_at, supersedes_id, '
                    'created_at')
_FLOW_COLUMNS = 'id, request_id, request_hash, flow_date, amount, note, recorded_at, voided_at, void_reason'


def snapshot_by_request(conn, request_id, *, account_id=REAL_ACCOUNT_ID):
    _, scope, params = _account_scope(conn, account_id)
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots WHERE request_id = %s{scope}',
                [request_id, *params])


def snapshot_head(conn, *, account_id=REAL_ACCOUNT_ID) -> dict[str, Any] | None:
    """The latest snapshot nothing else supersedes; a correction must target this row. The real account's by
    default (snapshots only exist for it, spec §7.2)."""
    _, scope, params = _account_scope(conn, account_id, alias='s', allow_all=True)
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots s WHERE NOT EXISTS '
                      f'(SELECT 1 FROM account_snapshots x WHERE x.supersedes_id = s.id){scope} '
                      f'ORDER BY recorded_at DESC, created_at DESC LIMIT 1', params)


def snapshot_by_id(conn, snapshot_id, *, account_id=REAL_ACCOUNT_ID) -> dict[str, Any] | None:
    _, scope, params = _account_scope(conn, account_id)
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots WHERE id = %s{scope}',
                [snapshot_id, *params])


def insert_snapshot(conn, rec: dict[str, Any], digest: str, *, recorded_at: datetime) -> dict[str, Any]:
    account_id = rec.get('account_id') or REAL_ACCOUNT_ID
    ready, _, _ = _account_scope(conn, account_id)
    extra_column, extra_value, extra_params = (', account_id', ', %s', [account_id]) if ready else ('', '', [])
    return _one(conn, 'INSERT INTO account_snapshots (request_id, request_hash, as_of, equity, note, recorded_at, '
                      f'supersedes_id{extra_column}) VALUES (%s, %s, %s, %s, %s, %s, %s{extra_value}) '
                      f'RETURNING {_SNAPSHOT_COLUMNS}',
                [rec['request_id'], digest, rec['as_of'], rec['equity'], rec['note'], recorded_at,
                 rec['supersedes_id'], *extra_params])


def snapshot_at(conn, as_of: date, *, cutoff_at: datetime, account_id=REAL_ACCOUNT_ID) -> dict[str, Any] | None:
    """Latest valid snapshot usable at ``cutoff_at``: recorded on time, not superseded, as_of on or before.
    The real account's by default."""
    _, scope, params = _account_scope(conn, account_id, alias='s', allow_all=True)
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots s WHERE s.as_of <= %s '
                      'AND s.recorded_at <= %s AND NOT EXISTS '
                      '(SELECT 1 FROM account_snapshots x WHERE x.supersedes_id = s.id)'
                      f'{scope} ORDER BY s.as_of DESC, s.recorded_at DESC LIMIT 1',
                [as_of, cutoff_at, *params])


def list_snapshots(conn, *, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Snapshots newest recorded first; the real account's by default (`account_id=None` reads every account's)."""
    _, scope, params = _account_scope(conn, account_id, allow_all=True)
    where = scope.replace(' AND ', ' WHERE ', 1)
    return _all(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots{where} '
                      'ORDER BY recorded_at DESC, created_at DESC', params)


def flow_by_request(conn, request_id, *, account_id=REAL_ACCOUNT_ID):
    _, scope, params = _account_scope(conn, account_id)
    return _one(conn, f'SELECT {_FLOW_COLUMNS} FROM cash_flows WHERE request_id = %s{scope}',
                [request_id, *params])


def flow_by_id(conn, flow_id, *, account_id=REAL_ACCOUNT_ID):
    _, scope, params = _account_scope(conn, account_id)
    return _one(conn, f'SELECT {_FLOW_COLUMNS} FROM cash_flows WHERE id = %s{scope}', [flow_id, *params])


def insert_flow(conn, rec: dict[str, Any], digest: str, *, recorded_at: datetime) -> dict[str, Any]:
    account_id = rec.get('account_id') or REAL_ACCOUNT_ID
    ready, _, _ = _account_scope(conn, account_id)
    extra_column, extra_value, extra_params = (', account_id', ', %s', [account_id]) if ready else ('', '', [])
    return _one(conn, 'INSERT INTO cash_flows (request_id, request_hash, flow_date, amount, note, recorded_at'
                      f'{extra_column}) '
                      f'VALUES (%s, %s, %s, %s, %s, %s{extra_value}) RETURNING {_FLOW_COLUMNS}',
                [rec['request_id'], digest, rec['flow_date'], rec['amount'], rec['note'], recorded_at, *extra_params])


def mark_flow_voided(conn, flow_id, reason: str, *, voided_at: datetime) -> dict[str, Any]:
    # Status fields only, as with trades; the flow itself is never rewritten.
    return _one(conn, f'UPDATE cash_flows SET voided_at = %s, void_reason = %s WHERE id = %s AND voided_at IS NULL '
                      f'RETURNING {_FLOW_COLUMNS}', (voided_at, reason, flow_id))


def list_flows(conn, *, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Flows oldest first; the real account's by default (`account_id=None` reads every account's)."""
    _, scope, params = _account_scope(conn, account_id, allow_all=True)
    where = scope.replace(' AND ', ' WHERE ', 1)
    return _all(conn, f'SELECT {_FLOW_COLUMNS} FROM cash_flows{where} ORDER BY flow_date, created_at', params)


def open_position_tickers(conn, *, account_id=REAL_ACCOUNT_ID) -> set[str]:
    """Tickers the account still holds a position in, from its unvoided trades (the real account's by default)."""

    held: set[str] = set()
    for ticker in sorted({t['ticker'] for t in list_trades(conn, account_id=account_id)
                          if t['voided_at'] is None}):
        try:
            book = positions.build(list_trades(conn, ticker, account_id=account_id),
                                   splits=data_repo.split_events(conn, ticker))
        except positions.PositionError:
            continue  # a broken trade history must not fail the whole screen
        if book['position'] is not None:
            held.add(ticker)
    return held


def plans_by_ids(conn, ids) -> list[dict[str, Any]]:
    return _all(conn, 'SELECT id, ticker, signal_date, kind, status, invalid_reasons FROM trade_plans '
                      'WHERE id = ANY(%s::uuid[]) ORDER BY ticker, signal_date, created_at', (list(map(str, ids)),))


def plan_reviews_by_ids(conn, ids) -> list[dict[str, Any]]:
    """Needs migration 026."""
    return _all(conn, 'SELECT id, ticker, signal_date, outcome, head_plan_id, new_plan_id FROM plan_reviews '
                      'WHERE id = ANY(%s) ORDER BY ticker, signal_date, id', (list(ids),))


def agent_plan(conn, ticker: str, signal_date: date) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE ticker = %s AND signal_date = %s '
                      "AND kind = 'agent' ORDER BY created_at DESC LIMIT 1", (ticker.upper(), signal_date))


EFFECTIVE_REVIEW_OUTCOMES = ('maintain', 'revise', 'invalidate')

_VERSION_COLUMNS = ('plan_id, ticker, signal_date, version_no, supersedes_plan_id, origin, agent_run_id, '
                    'cutoff_at, as_of_session, diff, schema_version, created_at')
_REVIEW_COLUMNS = ('id, ticker, signal_date, head_plan_id, session, trigger, run_id, mode, timeline_version, '
                   'signals, conditions, user_input_ids, outcome, decided_by, reason, refs, review_agent_run_id, '
                   'revision_agent_run_id, new_plan_id, detail, attempt, cutoff_at, input_tokens, output_tokens, '
                   'created_at')
_USER_INPUT_COLUMNS = ('id, request_id, request_hash, ticker, signal_date, kind, body, source_url, supersedes_id, '
                       'created_at')


def try_review_lock(conn, ticker: str, signal_date: date) -> bool:
    """Transaction-scoped, non-blocking lock on one chain shared by the batch review and a recheck."""
    key = f'review:{ticker.upper()}:{signal_date.isoformat()}'
    return bool(conn.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s))', (key,)).fetchone()[0])


def insert_plan_version(conn, row: dict[str, Any]) -> dict[str, Any]:
    """`row` keys are the §6.1 columns except `created_at`; `diff` arrives as a Python list or None."""
    diff = row.get('diff')
    return _one(conn,
                'INSERT INTO agent_plan_versions (plan_id, ticker, signal_date, version_no, supersedes_plan_id, '
                'origin, agent_run_id, cutoff_at, as_of_session, diff, schema_version) '
                f'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING {_VERSION_COLUMNS}',
                (row['plan_id'], row['ticker'], row['signal_date'], row['version_no'], row.get('supersedes_plan_id'),
                 row['origin'], row['agent_run_id'], row['cutoff_at'], row['as_of_session'],
                 _plan_json(diff) if diff is not None else None, row['schema_version']))


def chain_versions(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """One chain's version rows, version 0 first."""
    return _all(conn, f'SELECT {_VERSION_COLUMNS} FROM agent_plan_versions WHERE ticker = %s AND signal_date = %s '
                      'ORDER BY version_no', (ticker.upper(), signal_date))


def version_by_plan(conn, plan_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_VERSION_COLUMNS} FROM agent_plan_versions WHERE plan_id = %s', (plan_id,))


def version_by_run(conn, agent_run_id) -> dict[str, Any] | None:
    """The version row the agent run produced (``agent_run_id`` is unique); None for runs that made no version."""
    return _one(conn, f'SELECT {_VERSION_COLUMNS} FROM agent_plan_versions WHERE agent_run_id = %s',
                (agent_run_id,))


def chain_head(conn, ticker: str, signal_date: date) -> dict[str, Any] | None:
    """The chain's head — its highest-numbered agent-2 / agent-3 version — as {'plan': trade_plans row,
    'version': agent_plan_versions row}; None when the unit has no agent-2 chain."""
    version = _one(conn, f"SELECT {', '.join('v.' + c.strip() for c in _VERSION_COLUMNS.split(','))} "
                         'FROM agent_plan_versions v JOIN trade_plans p ON p.id = v.plan_id '
                         "WHERE v.ticker = %s AND v.signal_date = %s AND p.plan_version IN ('agent-2', 'agent-3') "
                         'ORDER BY v.version_no DESC LIMIT 1', (ticker.upper(), signal_date))
    if version is None:
        return None
    return {'plan': plan_by_id(conn, version['plan_id']), 'version': version}


def all_chains(conn) -> list[dict[str, Any]]:
    """Every chain: ticker, signal_date, the head's plan_id and version_no, and `pool` of the run
    that produced version 0 (agent_runs.pool); ordered by (signal_date, ticker)."""
    return _all(conn,
                'SELECT h.ticker, h.signal_date, h.plan_id, h.version_no, r.pool FROM ('
                'SELECT DISTINCT ON (ticker, signal_date) ticker, signal_date, plan_id, version_no '
                'FROM agent_plan_versions ORDER BY ticker, signal_date, version_no DESC) h '
                'LEFT JOIN agent_plan_versions v0 ON v0.ticker = h.ticker AND v0.signal_date = h.signal_date '
                'AND v0.version_no = 0 '
                'LEFT JOIN agent_runs r ON r.id = v0.agent_run_id '
                'ORDER BY h.signal_date, h.ticker', ())


def insert_plan_review(conn, row: dict[str, Any]) -> dict[str, Any]:
    """`row` keys are the §6.2 columns except `id`/`created_at`; JSONB columns arrive as Python
    objects and, like the token counts and user_input_ids, fall back to the table defaults."""
    return _one(conn,
                'INSERT INTO plan_reviews (ticker, signal_date, head_plan_id, session, trigger, run_id, mode, '
                'timeline_version, signals, conditions, user_input_ids, outcome, decided_by, reason, refs, '
                'review_agent_run_id, revision_agent_run_id, new_plan_id, detail, attempt, cutoff_at, '
                'input_tokens, output_tokens) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb, '
                f'%s, %s, %s, %s::jsonb, %s, %s, %s, %s) RETURNING {_REVIEW_COLUMNS}',
                (row['ticker'], row['signal_date'], row['head_plan_id'], row['session'], row['trigger'],
                 row.get('run_id'), row['mode'], row['timeline_version'], _plan_json(row.get('signals') or []),
                 _plan_json(row.get('conditions') or []), list(row.get('user_input_ids') or []), row['outcome'],
                 row['decided_by'], row['reason'], _plan_json(row.get('refs') or []),
                 row.get('review_agent_run_id'), row.get('revision_agent_run_id'), row.get('new_plan_id'),
                 _plan_json(row.get('detail') or {}), row['attempt'], row['cutoff_at'],
                 row.get('input_tokens') or 0, row.get('output_tokens') or 0))


def chain_reviews(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """One chain's review rows (every outcome and trigger), oldest first."""
    return _all(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews WHERE ticker = %s AND signal_date = %s '
                      'ORDER BY created_at, id', (ticker.upper(), signal_date))


def review_by_run(conn, agent_run_id) -> dict[str, Any] | None:
    """The review row a single-phase review run or a revision run belongs to (newest if several)."""
    return _one(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews '
                      'WHERE review_agent_run_id = %s OR revision_agent_run_id = %s '
                      'ORDER BY created_at DESC, id DESC LIMIT 1', (agent_run_id, agent_run_id))


def reviews_of_head(conn, head_plan_id) -> list[dict[str, Any]]:
    """Every review row that reviewed this version (``head_plan_id``), any outcome, oldest first."""
    return _all(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews WHERE head_plan_id = %s '
                      'ORDER BY created_at, id', (head_plan_id,))


def reviews_on(conn, session: date) -> list[dict[str, Any]]:
    """Every review row of one review session S, across chains, oldest first."""
    return _all(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews WHERE session = %s ORDER BY created_at, id',
                (session,))


def reviews_since(conn, since: datetime | None, session: date,
                  until: datetime | None = None) -> list[dict[str, Any]]:
    """The review rows the daily push of `session` reports (v2.5 D25): every row written after `since`
    (the read time of the last successful push of an earlier session) whose session is not later than
    `session`, batch and recheck alike; with no earlier push, only the rows of `session` itself. `until`
    (the push's own read time) bounds both cases, so a row written while the push is being sent falls in
    the next window. Oldest first."""
    if since is None:
        sql, params = 'session = %s', [session]
    else:
        sql, params = 'created_at > %s AND session <= %s', [since, session]
    if until is not None:
        sql += ' AND created_at <= %s'
        params.append(until)
    return _all(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews WHERE {sql} ORDER BY created_at, id', params)


def batch_attempts(conn, ticker: str, signal_date: date, session: date) -> list[dict[str, Any]]:
    """The batch review rows of one chain on one session (any outcome), in attempt order."""
    return _all(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews WHERE ticker = %s AND signal_date = %s '
                      "AND session = %s AND trigger = 'batch' ORDER BY attempt, created_at, id",
                (ticker.upper(), signal_date, session))


def last_effective_review(conn, head_plan_id) -> dict[str, Any] | None:
    """The newest maintain / revise / invalidate row that reviewed this version, batch or recheck."""
    return _one(conn, f'SELECT {_REVIEW_COLUMNS} FROM plan_reviews WHERE head_plan_id = %s '
                      'AND outcome = ANY(%s) ORDER BY created_at DESC, id DESC LIMIT 1',
                (head_plan_id, list(EFFECTIVE_REVIEW_OUTCOMES)))


def insert_user_input(conn, row: dict[str, Any]) -> dict[str, Any]:
    """`row` keys are the §6.3 columns except `id`/`created_at`."""
    return _one(conn,
                'INSERT INTO user_inputs (request_id, request_hash, ticker, signal_date, kind, body, source_url, '
                f'supersedes_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_USER_INPUT_COLUMNS}',
                (row['request_id'], row['request_hash'], row['ticker'], row.get('signal_date'), row['kind'],
                 row['body'], row.get('source_url'), row.get('supersedes_id')))


def user_input_by_request(conn, request_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_USER_INPUT_COLUMNS} FROM user_inputs WHERE request_id = %s', (request_id,))


def user_inputs_for(conn, ticker: str, signal_date: date | None) -> list[dict[str, Any]]:
    """The ticker-level inputs (signal_date NULL) plus, when given, the inputs of that chain, oldest
    first. Each row carries `superseded_by`: the earliest row whose supersedes_id points at it
    (correction or retract, wherever it was filed), or None. Callers apply their own cutoff."""
    columns = ', '.join('u.' + c.strip() for c in _USER_INPUT_COLUMNS.split(','))
    return _all(conn,
                f'SELECT {columns}, (SELECT min(s.id) FROM user_inputs s WHERE s.supersedes_id = u.id) '
                'AS superseded_by FROM user_inputs u WHERE u.ticker = %s '
                'AND (u.signal_date IS NULL OR u.signal_date = %s) ORDER BY u.created_at, u.id',
                (ticker.upper(), signal_date))


# --- review pushes (P4 §3.3) ---

def latest_account_record_at(conn) -> datetime | None:
    """The latest moment account inputs were recorded or corrected: snapshots and flows."""
    row = conn.execute(
        'SELECT greatest(s.rec, f.rec, f.void) FROM '
        '(SELECT max(recorded_at) AS rec FROM account_snapshots) s, '
        '(SELECT max(recorded_at) AS rec, max(voided_at) AS void FROM cash_flows) f').fetchone()
    return row[0] if row else None

_RETRIGGER_COLUMNS = ('id, ticker, unit_signal_date, chain_signal_date, chain_state, rule_ids, baseline_plan_id, '
                      'entry_cap, run_id, timeline_version, created_at')
_HOLDING_STATUS_COLUMNS = ('id, account_id, ticker, session, chain_signal_date, plan_id, review_id, run_id, '
                           'quantity, avg_cost, last_close, stop, target1, stop_mode, t1_reached, reached_1r, '
                           'to_stop_pct, to_t1_pct, r_now, held_sessions, max_hold_sessions, remaining_sessions, '
                           'instruction, exit_reason, reason, detail, timeline_version, created_at')
_PLAN_NOTE_COLUMNS = ('id, request_id, request_hash, ticker, chain_signal_date, plan_id, kind, body, supersedes_id, '
                      'created_at')


def _holding_status_columns(accounts_enabled: bool) -> str:
    return _HOLDING_STATUS_COLUMNS if accounts_enabled else _HOLDING_STATUS_COLUMNS.replace('id, account_id,', 'id,')


def insert_chain_retrigger(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one retrigger (a new unit merged into an active chain). `(ticker, unit_signal_date)` is unique: a
    second write returns the stored row with `replayed` True and changes nothing.

    `row` keys: ticker, unit_signal_date, chain_signal_date, chain_state, rule_ids, baseline_plan_id (may be
    None), entry_cap (entry state only), run_id, timeline_version; `created_at` (optional, default the database
    clock) is the cutoff of the run that found the trigger: the `plan` node passes the batch's `now`, so the
    row is visible to every reader whose cutoff is that run's (R4 windows read `created_at <= now`)."""
    ticker = row['ticker'].upper()
    inserted = _one(conn,
                    'INSERT INTO chain_retriggers (ticker, unit_signal_date, chain_signal_date, chain_state, '
                    'rule_ids, baseline_plan_id, entry_cap, run_id, timeline_version, created_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, clock_timestamp())) '
                    'ON CONFLICT (ticker, unit_signal_date) DO NOTHING '
                    f'RETURNING {_RETRIGGER_COLUMNS}',
                    (ticker, row['unit_signal_date'], row['chain_signal_date'], row['chain_state'],
                     list(row['rule_ids']), row.get('baseline_plan_id'), row.get('entry_cap'), row['run_id'],
                     row['timeline_version'], row.get('created_at')))
    if inserted is not None:
        return {**inserted, 'replayed': False}
    return {**retrigger_for_unit(conn, ticker, row['unit_signal_date']), 'replayed': True}


def retrigger_for_unit(conn, ticker: str, unit_signal_date: date) -> dict[str, Any] | None:
    """The retrigger row of one unit (the unit was merged into a chain), or None."""
    return _one(conn, f'SELECT {_RETRIGGER_COLUMNS} FROM chain_retriggers WHERE ticker = %s AND unit_signal_date = %s',
                (ticker.upper(), unit_signal_date))


def chain_retriggers_for(conn, ticker: str, *, chain_signal_date: date | None = None,
                         until_session: date | None = None, cutoff: datetime | None = None) -> list[dict[str, Any]]:
    """A ticker's retrigger rows, oldest first. `chain_signal_date` keeps one chain's, `until_session` those whose
    unit signal date is on or before it, `cutoff` those written at or before it (a row written later is invisible)."""
    clauses, params = ['ticker = %s'], [ticker.upper()]
    if chain_signal_date is not None:
        clauses.append('chain_signal_date = %s')
        params.append(chain_signal_date)
    if until_session is not None:
        clauses.append('unit_signal_date <= %s')
        params.append(until_session)
    if cutoff is not None:
        clauses.append('created_at <= %s')
        params.append(cutoff)
    return _all(conn, f"SELECT {_RETRIGGER_COLUMNS} FROM chain_retriggers WHERE {' AND '.join(clauses)} "
                      'ORDER BY created_at, id', params)


def insert_holding_status(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one holding-status row. `(account_id, ticker, session)` is unique (migration 030): a second write of
    the same session returns the stored row with `replayed` True and changes nothing. `account_id` defaults to the
    real account — the `review` node writes those; the `accounts` node passes a paper account's id.

    `row` keys: the §8.4 columns except id / created_at; ticker, session, run_id, quantity, avg_cost, held_sessions,
    instruction, reason and timeline_version are required, the rest default to NULL (`detail` to {})."""
    ticker = row['ticker'].upper()
    account_id = row.get('account_id') or REAL_ACCOUNT_ID
    ready, scope, account_params = _account_scope(conn, account_id)
    account_column, account_value = ('account_id, ', '%s, ') if ready else ('', '')
    conflict_columns = f'{account_column}ticker, session'
    columns = _holding_status_columns(ready)
    inserted = _one(conn,
                    f'INSERT INTO holding_status ({account_column}ticker, session, chain_signal_date, plan_id, '
                    'review_id, run_id, quantity, avg_cost, last_close, stop, target1, stop_mode, t1_reached, '
                    'reached_1r, to_stop_pct, to_t1_pct, r_now, held_sessions, max_hold_sessions, '
                    'remaining_sessions, instruction, exit_reason, reason, detail, timeline_version) '
                    f'VALUES ({account_value}%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '
                    '%s, %s, %s, '
                    f'%s, %s, %s, %s::jsonb, %s) ON CONFLICT ({conflict_columns}) DO NOTHING '
                    f'RETURNING {columns}',
                    [*account_params, ticker, row['session'], row.get('chain_signal_date'), row.get('plan_id'),
                     row.get('review_id'), row['run_id'], row['quantity'], row['avg_cost'], row.get('last_close'),
                     row.get('stop'), row.get('target1'), row.get('stop_mode'), row.get('t1_reached'),
                     row.get('reached_1r'), row.get('to_stop_pct'), row.get('to_t1_pct'), row.get('r_now'),
                     row['held_sessions'], row.get('max_hold_sessions'), row.get('remaining_sessions'),
                     row['instruction'], row.get('exit_reason'), row['reason'], _plan_json(row.get('detail') or {}),
                     row['timeline_version']])
    if inserted is not None:
        return {**inserted, 'replayed': False}
    existing = _one(conn, f'SELECT {columns} FROM holding_status WHERE ticker = %s AND session = %s{scope}',
                    [ticker, row['session'], *account_params])
    return {**existing, 'replayed': True}


def holding_status_for(conn, *, session: date | None = None, ticker: str | None = None,
                       chain_signal_date: date | None = None,
                       account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Holding-status rows matching every given filter, oldest session first (then ticker). One account's by
    default (`account_id=None` reads every account's)."""
    clauses, params = [], []
    ready, scope, account_params = _account_scope(conn, account_id, allow_all=True)
    if scope:
        clauses.append(scope.removeprefix(' AND '))
        params.extend(account_params)
    if session is not None:
        clauses.append('session = %s')
        params.append(session)
    if ticker is not None:
        clauses.append('ticker = %s')
        params.append(ticker.upper())
    if chain_signal_date is not None:
        clauses.append('chain_signal_date = %s')
        params.append(chain_signal_date)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    return _all(conn, f'SELECT {_holding_status_columns(ready)} FROM holding_status{where} '
                      'ORDER BY session, ticker, id', params)


def plan_note_by_request(conn, request_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_PLAN_NOTE_COLUMNS} FROM plan_notes WHERE request_id = %s', (request_id,))


def insert_plan_note(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one note (kind 'note') or retraction (kind 'retract', needs `supersedes_id`); a correction is a 'note'
    row with `supersedes_id`. Idempotent on `request_id`: the same `request_hash` returns the stored row with
    `replayed` True, a different one raises `ledger.Conflict`.

    `row` keys: request_id, request_hash, ticker, chain_signal_date XOR plan_id, kind, body, supersedes_id."""
    def known() -> dict[str, Any] | None:
        existing = plan_note_by_request(conn, row['request_id'])
        if existing is None:
            return None
        if existing['request_hash'] != row['request_hash']:
            raise Conflict('request_id was already used with different content')
        return {**existing, 'replayed': True}

    if (replayed := known()) is not None:
        return replayed
    inserted = _one(conn,
                    'INSERT INTO plan_notes (request_id, request_hash, ticker, chain_signal_date, plan_id, kind, '
                    'body, supersedes_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) '
                    f'ON CONFLICT (request_id) DO NOTHING RETURNING {_PLAN_NOTE_COLUMNS}',
                    (row['request_id'], row['request_hash'], row['ticker'].upper(), row.get('chain_signal_date'),
                     row.get('plan_id'), row['kind'], row['body'], row.get('supersedes_id')))
    if inserted is not None:
        return {**inserted, 'replayed': False}
    return known()


def plan_notes_for(conn, ticker: str, *, chain_signal_date: date | None = None, plan_id=None,
                   cutoff: datetime | None = None) -> list[dict[str, Any]]:
    """A ticker's notes (kind 'note' rows only), oldest first, each with `status` and `superseded_by`.

    `status` is 'retracted' when a retraction points at the note, 'corrected' when a newer note does, else 'active';
    `superseded_by` is the earliest row that points at it. Scope: `chain_signal_date` (a chain's notes), `plan_id`
    (a non-chain plan's notes), both (either), neither (all of the ticker's). With `cutoff` only rows written at
    or before it exist, so a correction made later does not take effect yet."""
    cut = '' if cutoff is None else ' AND s.created_at <= %(cutoff)s'
    scope = ['n.ticker = %(ticker)s', "n.kind = 'note'"]
    params: dict[str, Any] = {'ticker': ticker.upper(), 'cutoff': cutoff}
    if cutoff is not None:
        scope.append('n.created_at <= %(cutoff)s')
    targets = []
    if chain_signal_date is not None:
        targets.append('n.chain_signal_date = %(chain)s')
        params['chain'] = chain_signal_date
    if plan_id is not None:
        targets.append('n.plan_id = %(plan)s')
        params['plan'] = plan_id
    if targets:
        scope.append(f"({' OR '.join(targets)})")
    columns = ', '.join('n.' + c.strip() for c in _PLAN_NOTE_COLUMNS.split(','))
    return _all(conn,
                f'SELECT {columns}, '
                "CASE WHEN EXISTS (SELECT 1 FROM plan_notes s WHERE s.supersedes_id = n.id "
                f"AND s.kind = 'retract'{cut}) "
                f'THEN \'retracted\' WHEN EXISTS (SELECT 1 FROM plan_notes s WHERE s.supersedes_id = n.id{cut}) '
                f'THEN \'corrected\' ELSE \'active\' END AS status, '
                f'(SELECT min(s.id) FROM plan_notes s WHERE s.supersedes_id = n.id{cut}) AS superseded_by '
                f"FROM plan_notes n WHERE {' AND '.join(scope)} ORDER BY n.created_at, n.id", params)


def chains_for_ticker(conn, ticker: str, *, cutoff: datetime | None = None) -> list[dict[str, Any]]:
    """One row per agent chain of the ticker (oldest first), summarised from `agent_plan_versions` for `timeline-2`:
    `ticker`, `signal_date`, `first_schema_version` (version 0), `head_schema_version`, `head_plan_id`,
    `head_version_no`, `version_count`, `first_created_at`. With `cutoff` only versions created at or before it
    count, and a chain whose first version is later is absent."""
    clause, params = '', [ticker.upper()]
    if cutoff is not None:
        clause = ' AND created_at <= %s'
        params.append(cutoff)
    return _all(conn,
                'SELECT ticker, signal_date, '
                '(array_agg(schema_version ORDER BY version_no))[1] AS first_schema_version, '
                '(array_agg(schema_version ORDER BY version_no DESC))[1] AS head_schema_version, '
                '(array_agg(plan_id ORDER BY version_no DESC))[1] AS head_plan_id, '
                'max(version_no) AS head_version_no, count(*)::int AS version_count, '
                'min(created_at) AS first_created_at '
                f'FROM agent_plan_versions WHERE ticker = %s{clause} '
                'GROUP BY ticker, signal_date ORDER BY signal_date', params)


# --- v2.9 additions ---
# Tasks 3 and 4 append their queries below this line without touching the functions above.


def retriggers_on(conn, unit_signal_date: date) -> list[dict[str, Any]]:
    """Every retrigger row (all tickers) whose unit signal date is this session, oldest first: the merges the
    day's `plan` node made (`plan_review check`, ② of spec §9.2)."""
    return _all(conn, f'SELECT {_RETRIGGER_COLUMNS} FROM chain_retriggers WHERE unit_signal_date = %s '
                      'ORDER BY created_at, id', (unit_signal_date,))


_ACCOUNT_COLUMNS = ('id, kind, strategy, name, opening_cash, opened_on, params, status, closed_at, close_reason, '
                    'settled_through, created_at')


def list_accounts(conn, *, include_closed: bool = False) -> list[dict[str, Any]]:
    """Accounts oldest first; closed ones only when asked (D19: closing keeps the history, it never deletes)."""
    where = '' if include_closed else " WHERE status = 'active'"
    return _all(conn, f'SELECT {_ACCOUNT_COLUMNS} FROM accounts{where} ORDER BY created_at, id', ())


def account_by_id(conn, account_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE id = %s', (account_id,))


def account_by_name(conn, name) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE name = %s', (name,))


def insert_account(conn, rec: dict[str, Any]) -> dict[str, Any]:
    """One account row; `id` is the caller's (a fresh UUID for a paper account). `params` defaults to the empty
    object — the service layer layers `config.workbench`'s defaults on top."""
    return _one(conn,
                'INSERT INTO accounts (id, kind, strategy, name, opening_cash, opened_on, params) '
                f'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING {_ACCOUNT_COLUMNS}',
                (rec['id'], rec['kind'], rec['strategy'], rec['name'], rec.get('opening_cash'),
                 rec.get('opened_on'), _plan_json(rec.get('params') or {})))


def set_account_opening(conn, account_id, *, opening_cash, opened_on) -> dict[str, Any] | None:
    """Record the real account's opening cash and day once (D3); None when it was already opened — opening twice
    is the caller's conflict, not a second write."""
    return _one(conn, 'UPDATE accounts SET opening_cash = %s, opened_on = %s '
                      f'WHERE id = %s AND opening_cash IS NULL RETURNING {_ACCOUNT_COLUMNS}',
                (opening_cash, opened_on, account_id))


def update_account_params(conn, account_id, params: dict[str, Any]) -> dict[str, Any]:
    """Set the account's position parameters; the caller books the change row (insert_param_change) in the same
    transaction — effective from the next trading day, history untouched (§4.1)."""
    return _one(conn, f'UPDATE accounts SET params = %s::jsonb WHERE id = %s RETURNING {_ACCOUNT_COLUMNS}',
                (_plan_json(params), account_id))


def close_account(conn, account_id, reason, *, closed_at) -> dict[str, Any] | None:
    """Close an account once (D19): no more decisions or settlement, history stays; None when already closed."""
    return _one(conn, "UPDATE accounts SET status = 'closed', closed_at = %s, close_reason = %s "
                      f"WHERE id = %s AND status = 'active' RETURNING {_ACCOUNT_COLUMNS}",
                (closed_at, reason, account_id))


def advance_settled_through(conn, account_id, session) -> None:
    """Move a paper account's settlement watermark forward only (D26); a replayed settlement never moves it
    backwards."""
    conn.execute('UPDATE accounts SET settled_through = %s WHERE id = %s '
                 'AND (settled_through IS NULL OR settled_through < %s)', (session, account_id, session))


_PARAM_CHANGE_COLUMNS = 'id, account_id, kind, params_before, params_after, reason, effective_from, created_at'


def insert_param_change(conn, row: dict[str, Any]) -> dict[str, Any]:
    """One parameter or opening change (§7.1), `effective_from` the next trading day."""
    return _one(conn,
                'INSERT INTO account_param_changes (account_id, kind, params_before, params_after, reason, '
                f'effective_from) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING {_PARAM_CHANGE_COLUMNS}',
                (row['account_id'], row['kind'],
                 _plan_json(row['params_before']) if row.get('params_before') is not None else None,
                 _plan_json(row['params_after']) if row.get('params_after') is not None else None,
                 row.get('reason'), row['effective_from']))


def param_changes(conn, account_id) -> list[dict[str, Any]]:
    """The account's parameter and opening changes, oldest first."""
    return _all(conn, f'SELECT {_PARAM_CHANGE_COLUMNS} FROM account_param_changes WHERE account_id = %s '
                      'ORDER BY created_at, id', (account_id,))


def paper_tickers(conn) -> set[str]:
    """Active paper accounts' current positions plus pending buys (D29), with each account's split-adjusted book."""
    held = set()
    for account in list_accounts(conn):
        if account['kind'] == 'paper':
            held |= open_position_tickers(conn, account_id=account['id'])
    rows = conn.execute(
        'SELECT DISTINCT o.ticker FROM paper_orders o JOIN accounts a ON a.id = o.account_id '
        "WHERE a.kind = 'paper' AND a.status = 'active' AND o.status = 'pending' AND o.action = 'buy'").fetchall()
    return held | {row[0] for row in rows}


_EQUITY_DAY_COLUMNS = ('id, account_id, session, cash, positions_value, equity, realized_pnl_cum, unrealized_pnl, '
                       'net_flows_cum, day_pnl, positions_count, detail, account_version, run_id, computed_at')


def insert_equity_day(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one day's equity row (§7.4, append-only): a recompute of the same session appends a newer
    `computed_at` row and readers take the latest (D15). `computed_at` may be pinned, the default is the
    database clock."""
    return _one(conn,
                'INSERT INTO account_equity_days (account_id, session, cash, positions_value, equity, '
                'realized_pnl_cum, unrealized_pnl, net_flows_cum, day_pnl, positions_count, detail, '
                'account_version, run_id, computed_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, COALESCE(%s, clock_timestamp()))'
                f' RETURNING {_EQUITY_DAY_COLUMNS}',
                (row['account_id'], row['session'], row['cash'], row['positions_value'], row['equity'],
                 row['realized_pnl_cum'], row['unrealized_pnl'], row['net_flows_cum'], row.get('day_pnl'),
                 row['positions_count'], _plan_json(row.get('detail') or {}), row['account_version'],
                 row['run_id'], row.get('computed_at')))


def equity_days(conn, account_id, *, from_session: date | None = None,
                to_session: date | None = None, cutoff_at: datetime | None = None) -> list[dict[str, Any]]:
    """The account's equity per session — the latest `computed_at` row of each (D15) — oldest session first;
    `from_session` / `to_session` bound the range (inclusive)."""
    clauses, params = ['account_id = %s'], [account_id]
    if from_session is not None:
        clauses.append('session >= %s')
        params.append(from_session)
    if to_session is not None:
        clauses.append('session <= %s')
        params.append(to_session)
    if cutoff_at is not None:
        clauses.append('computed_at <= %s')
        params.append(cutoff_at)
    return _all(conn, f'SELECT DISTINCT ON (session) {_EQUITY_DAY_COLUMNS} FROM account_equity_days '
                      f"WHERE {' AND '.join(clauses)} ORDER BY session, computed_at DESC, id DESC", params)


def latest_equity_day(conn, account_id, *, on_or_before: date) -> dict[str, Any] | None:
    """The newest equity row of the session at or before the date (D6: that day's ledger equity)."""
    return _one(conn, f'SELECT {_EQUITY_DAY_COLUMNS} FROM account_equity_days WHERE account_id = %s '
                      'AND session <= %s ORDER BY session DESC, computed_at DESC, id DESC LIMIT 1',
                (account_id, on_or_before))


def equity_day_by_id(conn, row_id: int, *, account_id=REAL_ACCOUNT_ID) -> dict[str, Any] | None:
    """Read the exact frozen equity source, scoped to its account; never replace it with a newer revision."""
    return _one(conn, f'SELECT {_EQUITY_DAY_COLUMNS} FROM account_equity_days WHERE id = %s AND account_id = %s',
                (row_id, account_id))


_ORDER_COLUMNS = ('id, account_id, decision_id, action, ticker, plan_id, chain_signal_date, shares, portion, '
                  'target_order_id, placed_on, placed_by, reason, status, status_reason, settled_on, trade_id, '
                  'created_at, updated_at')


def insert_order(conn, row: dict[str, Any]) -> dict[str, Any]:
    """One paper-order intent (§7.5); `id` is the caller's UUID. The status fields arrive as given — a rejected
    order is inserted as rejected (D9), everything else starts pending and settles through settle_order."""
    return _one(conn,
                'INSERT INTO paper_orders (id, account_id, decision_id, action, ticker, plan_id, '
                'chain_signal_date, shares, portion, target_order_id, placed_on, placed_by, reason, status, '
                'status_reason, settled_on, trade_id) '
                f"VALUES ({', '.join(['%s'] * 17)}) RETURNING {_ORDER_COLUMNS}",
                (row['id'], row['account_id'], row.get('decision_id'), row['action'], row['ticker'],
                 row.get('plan_id'), row.get('chain_signal_date'), row.get('shares'), row.get('portion'),
                 row.get('target_order_id'), row['placed_on'], row['placed_by'], row.get('reason'),
                 row['status'], row.get('status_reason'), row.get('settled_on'), row.get('trade_id')))


def order_by_id(conn, order_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_ORDER_COLUMNS} FROM paper_orders WHERE id = %s', (order_id,))


def pending_orders(conn, account_id) -> list[dict[str, Any]]:
    """The account's pending orders, oldest first."""
    return _all(conn, f"SELECT {_ORDER_COLUMNS} FROM paper_orders WHERE account_id = %s AND status = 'pending' "
                      'ORDER BY created_at, id', (account_id,))


def orders_for(conn, account_id, *, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """The account's orders, newest first; `status` narrows them."""
    clauses, params = ['account_id = %s'], [account_id]
    if status is not None:
        clauses.append('status = %s')
        params.append(status)
    params.append(limit)
    return _all(conn, f"SELECT {_ORDER_COLUMNS} FROM paper_orders WHERE {' AND '.join(clauses)} "
                      'ORDER BY created_at DESC, id DESC LIMIT %s', params)


def settle_order(conn, order_id, *, status, status_reason, settled_on, trade_id=None) -> dict[str, Any] | None:
    """Move a pending order to a terminal outcome once (§7.5: the status columns are updatable, `updated_at`
    records it); None when the order is no longer pending, so a replayed settlement cannot overwrite the first
    outcome."""
    if status not in ('filled', 'voided', 'expired', 'cancelled', 'rejected'):
        raise ValueError('order settlement requires a terminal status')
    return _one(conn,
                'UPDATE paper_orders SET status = %s, status_reason = %s, settled_on = %s, trade_id = %s, '
                "updated_at = clock_timestamp() WHERE id = %s AND status = 'pending' "
                f'RETURNING {_ORDER_COLUMNS}',
                (status, status_reason, settled_on, trade_id, order_id))


def tried_plan_ids(conn, account_id) -> set[uuid.UUID]:
    """The plan versions this account has placed a buy order on, except host-rejected ones (D24's per-version
    filter: an order that expired only ends its version, not the chain; a rejected intent never reached the
    market, so its version stays available for a corrected retry)."""
    rows = conn.execute('SELECT DISTINCT plan_id FROM paper_orders WHERE account_id = %s '
                        "AND action = 'buy' AND plan_id IS NOT NULL AND status <> 'rejected'", (account_id,)).fetchall()
    return {row[0] for row in rows}


_PAPER_DECISION_COLUMNS = ('id, account_id, session, status, status_reason, agent_run_id, agent_version, '
                     'strategy_version, evidence, output, summary, holds, detail, run_id, created_at')


def insert_decision(conn, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The account's decision row of the session (D14): `(row, inserted)`. `(account_id, session)` is unique, so a
    same-session rerun inserts nothing and returns the stored row with False — the model is not called twice."""
    inserted = _one(conn,
                    'INSERT INTO paper_decisions (account_id, session, status, status_reason, agent_run_id, '
                    'agent_version, strategy_version, evidence, output, summary, holds, detail, run_id) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s) '
                    'ON CONFLICT (account_id, session) DO NOTHING '
                    f'RETURNING {_PAPER_DECISION_COLUMNS}',
                    (row['account_id'], row['session'], row['status'], row.get('status_reason'),
                     row.get('agent_run_id'), row.get('agent_version'), row.get('strategy_version'),
                     _plan_json(row['evidence']) if row.get('evidence') is not None else None,
                     _plan_json(row['output']) if row.get('output') is not None else None,
                     row.get('summary'),
                     _plan_json(row['holds']) if row.get('holds') is not None else None,
                     _plan_json(row.get('detail') or {}), row.get('run_id')))
    if inserted is not None:
        return inserted, True
    return decision_for(conn, row['account_id'], row['session']), False


def decision_for(conn, account_id, session) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_PAPER_DECISION_COLUMNS} FROM paper_decisions WHERE account_id = %s AND session = %s',
                (account_id, session))


def decisions_for(conn, account_id, *, limit: int = 60) -> list[dict[str, Any]]:
    """The account's decision rows, newest session first."""
    return _all(conn, f'SELECT {_PAPER_DECISION_COLUMNS} FROM paper_decisions WHERE account_id = %s '
                      'ORDER BY session DESC, created_at DESC LIMIT %s', (account_id, limit))


def insert_chain_done(conn, *, account_id, ticker, chain_signal_date, reason) -> None:
    """Mark the chain consumed for this account (D24: `entry_voided` after an entry-1 void, `exited` after a stop
    or an exit); a repeated write changes nothing."""
    conn.execute('INSERT INTO paper_chain_done (account_id, ticker, chain_signal_date, reason) '
                 'VALUES (%s, %s, %s, %s) ON CONFLICT (account_id, ticker, chain_signal_date) DO NOTHING',
                 (account_id, ticker.upper(), chain_signal_date, reason))


def chain_done(conn, account_id) -> set[tuple[str, date]]:
    """The `(ticker, chain_signal_date)` pairs this account has consumed (D24's per-chain filter)."""
    return {(row[0], row[1]) for row in conn.execute(
        'SELECT ticker, chain_signal_date FROM paper_chain_done WHERE account_id = %s', (account_id,)).fetchall()}
