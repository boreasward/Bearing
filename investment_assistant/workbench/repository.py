"""All workbench SQL. Callers own transactions: this layer never commits or rolls back."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

BAR_VALUES = ('open', 'high', 'low', 'close', 'adj_close', 'volume', 'split_factor')
_FLOAT_FIELDS = ('open', 'high', 'low', 'close', 'adj_close', 'split_factor')

# The default real account (migration 030, spec D2): its id is fixed, the seed row is the only real account, and
# every pre-030 row landed in it through the column defaults. All ledger readers below default to it, so the
# existing call sites keep seeing exactly the real account's view (spec D4). A later task replaces the literal
# with the constant of `workbench.accounts`.
REAL_ACCOUNT_ID = uuid.UUID('00000000-0000-0000-0000-000000000001')


# yfinance re-derives adj_close with ~1e-7 relative float noise between fetches; a real dividend
# adjustment is around 1e-3 or larger. Raw prices and volume still compare exactly.
ADJ_CLOSE_TOLERANCE = Decimal('0.000001')


def _quantized(row: dict[str, Any]) -> tuple:
    return tuple(int(row[k]) if k == 'volume' else Decimal(row[k]).quantize(Decimal('0.000001'))
                 for k in BAR_VALUES if k != 'adj_close')


def _same_bar(stored: dict[str, Any], fetched: dict[str, Any]) -> bool:
    if _quantized(stored) != _quantized(fetched):
        return False
    old, new = Decimal(stored['adj_close']), Decimal(fetched['adj_close'])
    return abs(new - old) <= abs(old) * ADJ_CLOSE_TOLERANCE


def store_bars(conn, ticker: str, rows: list[dict[str, Any]], *, now: datetime) -> dict[str, int]:
    """Append a revision only when values differ from the latest one.

    Dividends rewrite historical adj_close, so a re-fetch after one appends new
    revisions for older dates; earlier revisions stay readable by cutoff.
    """
    ticker = ticker.upper()
    counts = {'inserted': 0, 'unchanged': 0}
    with conn.cursor(row_factory=dict_row) as cur:
        for row in rows:
            cur.execute(
                f"SELECT revision, {', '.join(BAR_VALUES)} FROM price_bars "
                'WHERE ticker = %s AND price_date = %s ORDER BY revision DESC LIMIT 1',
                (ticker, row['price_date']))
            latest = cur.fetchone()
            if latest and _same_bar(latest, row):
                counts['unchanged'] += 1
                continue
            cur.execute(
                f"INSERT INTO price_bars (ticker, price_date, {', '.join(BAR_VALUES)}, revision, fetched_at) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(BAR_VALUES))}, %s, %s)",
                (ticker, row['price_date'], *(row[k] for k in BAR_VALUES),
                 (latest['revision'] + 1) if latest else 1, now))
            counts['inserted'] += 1
    return counts


def read_bars(conn, ticker: str, end_date: date, *, cutoff_at: datetime, limit: int = 260) -> list[dict[str, Any]]:
    """Latest revision per date known by ``cutoff_at``, oldest first, at most ``limit`` dates."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT DISTINCT ON (price_date) id, price_date, revision, fetched_at, {', '.join(BAR_VALUES)} "
            'FROM price_bars WHERE ticker = %s AND price_date <= %s AND fetched_at <= %s '
            'ORDER BY price_date DESC, revision DESC LIMIT %s',
            (ticker.upper(), end_date, cutoff_at, limit))
        rows = cur.fetchall()
    for row in rows:
        for key in _FLOAT_FIELDS:
            row[key] = float(row[key])
    return list(reversed(rows))


_BAR_BY_ID_COLUMNS = f"id, ticker, price_date, revision, fetched_at, {', '.join(BAR_VALUES)}"


def bars_by_ids(conn, ids: list[int]) -> list[dict[str, Any]]:
    """The exact bar rows (any revision) named by ``ids``, in the order given.

    Numbers stay ``Decimal``; an id that reads back nothing is simply absent."""
    if not ids:
        return []
    found = {row['id']: row for row in _all(
        conn, f'SELECT {_BAR_BY_ID_COLUMNS} FROM price_bars WHERE id = ANY(%s)', ([int(i) for i in ids],))}
    return [found[int(i)] for i in ids if int(i) in found]


def _news_hash(item: dict[str, Any]) -> str:
    payload = {k: item.get(k) for k in ('title', 'publisher')}
    payload['published_at'] = item['published_at'].isoformat() if item.get('published_at') else None
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def store_news(conn, entries: list[tuple[dict[str, Any], list[str]]], *, now: datetime) -> dict[str, int]:
    """First sighting wins: a re-fetched (source, url) keeps its original row and fetched_at."""
    counts = {'inserted': 0, 'existing': 0}
    with conn.cursor() as cur:
        for item, tickers in entries:
            cur.execute(
                'INSERT INTO news_items (source, title, url, publisher, published_at, fetched_at, content_hash) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source, url) DO NOTHING RETURNING id',
                (item['source'], item['title'], item['url'], item.get('publisher'), item.get('published_at'),
                 now, _news_hash(item)))
            row = cur.fetchone()
            if row:
                counts['inserted'] += 1
            else:
                counts['existing'] += 1
                cur.execute('SELECT id FROM news_items WHERE source = %s AND url = %s', (item['source'], item['url']))
                row = cur.fetchone()
            for ticker in tickers:
                cur.execute('INSERT INTO news_item_tickers (news_item_id, ticker) VALUES (%s, %s) '
                            'ON CONFLICT DO NOTHING', (row[0], ticker.upper()))
    return counts


def read_news(conn, ticker: str, *, cutoff_at: datetime, since: datetime | None = None,
              limit: int | None = 50, source: str | None = None) -> list[dict[str, Any]]:
    """News known by ``cutoff_at``; undated items are kept and must be shown as such.

    ``source`` filters in SQL before any window/dedup, so one source's rows can
    never be pushed out by another's volume (I10); ``limit=None`` reads the whole
    window — the caller's window is the only cap."""
    clauses = ['t.ticker = %s', 'n.fetched_at <= %s', '(n.published_at IS NULL OR n.published_at <= %s)']
    params: list[Any] = [ticker.upper(), cutoff_at, cutoff_at]
    if since is not None:
        clauses.append('COALESCE(n.published_at, n.fetched_at) >= %s')
        params.append(since)
    if source is not None:
        clauses.append('n.source = %s')
        params.append(source)
    sql = ('SELECT n.id, n.source, n.title, n.url, n.publisher, n.published_at, n.fetched_at '
           'FROM news_items n JOIN news_item_tickers t ON t.news_item_id = n.id '
           f"WHERE {' AND '.join(clauses)} "
           'ORDER BY COALESCE(n.published_at, n.fetched_at) DESC, n.id DESC')
    if limit is not None:
        sql += ' LIMIT %s'
        params.append(limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def store_candidate(conn, ticker: str, signal_date: date, candidate, *, run_id: str | None) -> dict[str, Any]:
    """Insert once per (ticker, date, rule, version). A re-run with other inputs is a conflict, never an overwrite."""
    with conn.cursor() as cur:
        cur.execute(
            'INSERT INTO signal_candidates (ticker, signal_date, action, rule_id, rule_version, inputs, run_id) '
            'VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) '
            'ON CONFLICT (ticker, signal_date, rule_id, rule_version) DO NOTHING RETURNING id',
            (ticker.upper(), signal_date, candidate.action, candidate.rule_id, candidate.rule_version,
             _canonical(candidate.inputs), run_id))
        row = cur.fetchone()
        if row:
            return {'status': 'inserted', 'id': row[0]}
        cur.execute(
            'SELECT id, action, inputs FROM signal_candidates '
            'WHERE ticker = %s AND signal_date = %s AND rule_id = %s AND rule_version = %s',
            (ticker.upper(), signal_date, candidate.rule_id, candidate.rule_version))
        existing_id, action, inputs = cur.fetchone()
    same = action == candidate.action and _canonical(inputs) == _canonical(json.loads(_canonical(candidate.inputs)))
    return {'status': 'existing', 'id': existing_id} if same else {'status': 'conflict', 'id': existing_id}


def open_candidate_groups(conn, *, since: date) -> list[dict[str, Any]]:
    """Candidates grouped by (ticker, signal_date) that have no decision yet, newest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            'SELECT c.id, c.ticker, c.signal_date, c.action, c.rule_id, c.rule_version, c.inputs, c.created_at '
            'FROM signal_candidates c WHERE c.signal_date >= %s AND NOT EXISTS ('
            '  SELECT 1 FROM signal_decisions d WHERE d.ticker = c.ticker AND d.signal_date = c.signal_date) '
            'ORDER BY c.signal_date DESC, c.ticker, c.created_at, c.rule_id', (since,))
        rows = cur.fetchall()
    groups: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (row['ticker'], row['signal_date'])
        groups.setdefault(key, {'ticker': key[0], 'signal_date': key[1], 'candidates': []})['candidates'].append(row)
    return list(groups.values())


def store_brief(conn, brief: dict[str, Any]):
    with conn.cursor() as cur:
        cur.execute(
            # clock_timestamp(): several briefs in one transaction must still order by creation.
            'INSERT INTO signal_briefs (ticker, signal_date, model, prompt_version, cutoff_at, input_refs, output, '
            'status, error, agent_run_id, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, clock_timestamp()) '
            'RETURNING id',
            (brief['ticker'], brief['signal_date'], brief.get('model'), brief['prompt_version'], brief['cutoff_at'],
             _canonical(brief['input_refs']), _canonical(brief['output']), brief['status'],
             brief.get('error'), brief.get('agent_run_id')))
        return cur.fetchone()[0]


def latest_brief(conn, ticker: str, signal_date: date) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            'SELECT id, ticker, signal_date, model, prompt_version, cutoff_at, input_refs, output, status, error, '
            'agent_run_id, created_at FROM signal_briefs WHERE ticker = %s AND signal_date = %s '
            'ORDER BY created_at DESC, id DESC LIMIT 1', (ticker.upper(), signal_date))
        return cur.fetchone()


def lock(conn, key: str) -> None:
    """Transaction-scoped advisory lock serialising writes to one unit or ticker."""
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (key,))


_DECISION_COLUMNS = ('id, request_id, request_hash, ticker, signal_date, candidate_ids, brief_id, proposed_action, '
                     'final_action, decision, reason, decided_at, supersedes_id, plan_id')
_TRADE_COLUMNS = ('id, request_id, request_hash, ticker, side, quantity, price, fee, executed_at, decision_id, '
                  'plan_id, note, recorded_at, voided_at, void_reason, account_id, recorded_by, detail')


def _one(conn, sql: str, params) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(conn, sql: str, params) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _decision_columns(timeline: bool) -> str:
    """``timeline=True`` (migration 026 checked by the caller) adds ``agent_plan_id`` and ``review_view``."""
    return f'{_DECISION_COLUMNS}, agent_plan_id, review_view' if timeline else _DECISION_COLUMNS


def decision_by_request(conn, request_id, *, timeline: bool = False):
    return _one(conn, f'SELECT {_decision_columns(timeline)} FROM signal_decisions WHERE request_id = %s',
                (request_id,))


def decision_by_id(conn, decision_id):
    return _one(conn, f'SELECT {_DECISION_COLUMNS} FROM signal_decisions WHERE id = %s', (decision_id,))


def decision_head(conn, ticker: str, signal_date: date, *, timeline: bool = False) -> dict[str, Any] | None:
    """The unit's current decision: the one no other decision supersedes."""
    return _one(conn,
                f'SELECT {_decision_columns(timeline)} FROM signal_decisions d WHERE ticker = %s AND signal_date = %s '
                'AND NOT EXISTS (SELECT 1 FROM signal_decisions s WHERE s.supersedes_id = d.id) '
                'ORDER BY decided_at DESC LIMIT 1', (ticker, signal_date))


def unit_decision_ids(conn, ticker: str, signal_date: date) -> set:
    """Every decision id in the unit's chain, superseded ones included."""
    rows = conn.execute('SELECT id FROM signal_decisions WHERE ticker = %s AND signal_date = %s',
                        (ticker.upper(), signal_date)).fetchall()
    return {row[0] for row in rows}


def decision_heads(conn, *, since: date | None = None, ticker: str | None = None) -> list[dict[str, Any]]:
    clauses = ['NOT EXISTS (SELECT 1 FROM signal_decisions s WHERE s.supersedes_id = d.id)']
    params: list[Any] = []
    if since is not None:
        clauses.append('signal_date >= %s')
        params.append(since)
    if ticker is not None:
        clauses.append('ticker = %s')
        params.append(ticker.upper())
    return _all(conn, f"SELECT {_DECISION_COLUMNS} FROM signal_decisions d WHERE {' AND '.join(clauses)} "
                      'ORDER BY signal_date, ticker', params)


def count_unit_candidates(conn, ticker: str, signal_date: date, ids) -> int:
    return conn.execute('SELECT count(*) FROM signal_candidates WHERE ticker = %s AND signal_date = %s AND id = ANY(%s)',
                        (ticker, signal_date, list(ids))).fetchone()[0]


def brief_in_unit(conn, brief_id, ticker: str, signal_date: date) -> bool:
    return conn.execute('SELECT 1 FROM signal_briefs WHERE id = %s AND ticker = %s AND signal_date = %s',
                        (brief_id, ticker, signal_date)).fetchone() is not None


def _insert_decision_for_tests(conn, rec: dict[str, Any], digest: str, *,
                               decided_at: datetime) -> dict[str, Any]:
    """Test fixtures only (v2.9 D5): production code no longer records decisions, `signal_decisions` is read-only
    there. `tests/legacy_decisions.py` calls it (`ledger.record_decision` is gone).

    `agent_plan_id` / `review_view` (migration 026) are written, and returned, only when one is
    set; without them the statement is the pre-026 one, so old callers and an unmigrated database
    behave as before."""
    if rec.get('agent_plan_id') is not None or rec.get('review_view') is not None:
        view = rec.get('review_view')
        return _one(conn,
                    'INSERT INTO signal_decisions (request_id, request_hash, ticker, signal_date, candidate_ids, '
                    'brief_id, proposed_action, final_action, decision, reason, decided_at, supersedes_id, plan_id, '
                    'agent_plan_id, review_view) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) '
                    f'RETURNING {_DECISION_COLUMNS}, agent_plan_id, review_view',
                    (rec['request_id'], digest, rec['ticker'], rec['signal_date'], rec['candidate_ids'],
                     rec['brief_id'], rec['proposed_action'], rec['final_action'], rec['decision'], rec['reason'],
                     decided_at, rec['supersedes_id'], rec.get('plan_id'), rec.get('agent_plan_id'),
                     _plan_json(view) if view is not None else None))
    return _one(conn,
                'INSERT INTO signal_decisions (request_id, request_hash, ticker, signal_date, candidate_ids, brief_id, '
                'proposed_action, final_action, decision, reason, decided_at, supersedes_id, plan_id) '
                f'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_DECISION_COLUMNS}',
                (rec['request_id'], digest, rec['ticker'], rec['signal_date'], rec['candidate_ids'], rec['brief_id'],
                 rec['proposed_action'], rec['final_action'], rec['decision'], rec['reason'], decided_at,
                 rec['supersedes_id'], rec.get('plan_id')))


def trade_by_request(conn, request_id):
    return _one(conn, f'SELECT {_TRADE_COLUMNS} FROM trades WHERE request_id = %s', (request_id,))


def trade_by_id(conn, trade_id):
    return _one(conn, f'SELECT {_TRADE_COLUMNS} FROM trades WHERE id = %s', (trade_id,))


def _trades_where(conn, clause: str | None, params: list[Any], cutoff: datetime | None,
                  account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Trades of one account (`account_id=None` reads every account's), oldest first."""
    clauses = [clause] if clause else []
    params = list(params)
    if account_id is not None:
        clauses.append('account_id = %s')
        params.append(account_id)
    columns, head = _TRADE_COLUMNS, []
    if cutoff is not None:
        clauses.append('recorded_at <= %s AND executed_at <= %s')
        params.extend([cutoff, cutoff])
        # a void made after the cutoff is not known yet (compared in SQL, so a naive cutoff behaves like the rest)
        columns = _TRADE_COLUMNS.replace(
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
    return _one(conn,
                'INSERT INTO trades (request_id, request_hash, ticker, side, quantity, price, fee, executed_at, '
                'decision_id, plan_id, note, recorded_at, account_id, recorded_by, detail) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) '
                f'RETURNING {_TRADE_COLUMNS}',
                (rec['request_id'], digest, rec['ticker'], rec['side'], rec['quantity'], rec['price'], rec['fee'],
                 rec['executed_at'], rec.get('decision_id'), rec.get('plan_id'), rec['note'], recorded_at,
                 rec.get('account_id') or REAL_ACCOUNT_ID, rec.get('recorded_by', 'user'),
                 _plan_json(rec.get('detail') or {})))


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
    return _one(conn, f'UPDATE trades SET voided_at = %s, void_reason = %s WHERE id = %s AND voided_at IS NULL '
                      f'RETURNING {_TRADE_COLUMNS}', (voided_at, reason, trade_id))


def split_events(conn, ticker: str) -> list[tuple[date, Decimal]]:
    rows = conn.execute(
        'SELECT price_date, split_factor FROM (SELECT DISTINCT ON (price_date) price_date, split_factor '
        '  FROM price_bars WHERE ticker = %s ORDER BY price_date, revision DESC) latest '
        'WHERE split_factor <> 1 ORDER BY price_date', (ticker.upper(),)).fetchall()
    return [(d, Decimal(f)) for d, f in rows]


_WEEKLY_COLUMNS = 'id, week_start, calc_version, stats, notes, supersedes_id, created_at'


def weekly_head(conn, week_start: date) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_WEEKLY_COLUMNS} FROM weekly_reviews w WHERE week_start = %s AND NOT EXISTS '
                      '(SELECT 1 FROM weekly_reviews s WHERE s.supersedes_id = w.id)', (week_start,))


def weekly_heads(conn, limit: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_WEEKLY_COLUMNS} FROM weekly_reviews w WHERE NOT EXISTS '
                      '(SELECT 1 FROM weekly_reviews s WHERE s.supersedes_id = w.id) '
                      'ORDER BY week_start DESC LIMIT %s', (limit,))


def insert_weekly_review(conn, week_start: date, calc_version: str, stats: dict[str, Any], notes: str,
                         supersedes_id) -> dict[str, Any]:
    return _one(conn, 'INSERT INTO weekly_reviews (week_start, calc_version, stats, notes, supersedes_id) '
                      f'VALUES (%s, %s, %s::jsonb, %s, %s) RETURNING {_WEEKLY_COLUMNS}',
                (week_start, calc_version, _canonical(stats), notes, supersedes_id))


_PLAN_COLUMNS = ('id, ticker, signal_date, kind, based_on_id, plan_version, ref_price, atr, entry_low, '
                 'entry_high, stop, target1, trail_atr_multiple, valid_from, valid_until, sizing, status, '
                 'invalid_reasons, warnings, inputs, request_id, request_hash, created_at, entry_type, '
                 'max_hold_sessions')


def _plan_json(value: Any) -> str:
    """Canonical JSON with Decimals and dates as strings, stable across a jsonb round-trip."""
    def scalar(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item.normalize(), 'f')
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, uuid.UUID):
            return str(item)
        raise TypeError(f'not JSON-serializable: {type(item)!r}')

    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=scalar)


def store_plan(conn, plan: dict[str, Any]) -> dict[str, Any]:
    """Insert once per rule unit; same levels/sizing/status returns the stored row, different is a conflict."""
    if plan.get('kind') == 'rule':
        existing = rule_plan(conn, plan['ticker'], plan['signal_date'], plan.get('plan_version', 'plan-1'))
        if existing is not None:
            same = (existing['status'] == plan['status']
                    and all(Decimal(existing[k]) == plan[k]
                            for k in ('ref_price', 'atr', 'entry_low', 'entry_high', 'stop', 'target1'))
                    and _plan_json(existing['sizing']) == _plan_json(plan['sizing'])
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
    plan_columns = ', '.join('p.' + c.strip() for c in _PLAN_COLUMNS.split(','))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f'SELECT t.id AS trade_id, {plan_columns} FROM trades t '
                    'JOIN signal_decisions d ON d.id = t.decision_id '
                    'JOIN trade_plans p ON p.id = d.plan_id WHERE t.id = ANY(%s) AND t.account_id = %s',
                    (list(trade_ids), account_id))
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


def snapshot_by_request(conn, request_id):
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots WHERE request_id = %s', (request_id,))


def snapshot_head(conn, *, account_id=REAL_ACCOUNT_ID) -> dict[str, Any] | None:
    """The latest snapshot nothing else supersedes; a correction must target this row. The real account's by
    default (snapshots only exist for it, spec §7.2)."""
    clause, params = ('', []) if account_id is None else (' AND s.account_id = %s', [account_id])
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots s WHERE NOT EXISTS '
                      f'(SELECT 1 FROM account_snapshots x WHERE x.supersedes_id = s.id){clause} '
                      f'ORDER BY recorded_at DESC, created_at DESC LIMIT 1', params)


def snapshot_by_id(conn, snapshot_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots WHERE id = %s', (snapshot_id,))


def insert_snapshot(conn, rec: dict[str, Any], digest: str, *, recorded_at: datetime) -> dict[str, Any]:
    return _one(conn, 'INSERT INTO account_snapshots (request_id, request_hash, as_of, equity, note, recorded_at, '
                      f'supersedes_id, account_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) '
                      f'RETURNING {_SNAPSHOT_COLUMNS}',
                (rec['request_id'], digest, rec['as_of'], rec['equity'], rec['note'], recorded_at,
                 rec['supersedes_id'], rec.get('account_id') or REAL_ACCOUNT_ID))


def snapshot_at(conn, as_of: date, *, cutoff_at: datetime, account_id=REAL_ACCOUNT_ID) -> dict[str, Any] | None:
    """Latest valid snapshot usable at ``cutoff_at``: recorded on time, not superseded, as_of on or before.
    The real account's by default."""
    clause, params = ('', []) if account_id is None else (' AND s.account_id = %s', [account_id])
    return _one(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots s WHERE s.as_of <= %s '
                      'AND s.recorded_at <= %s AND NOT EXISTS '
                      '(SELECT 1 FROM account_snapshots x WHERE x.supersedes_id = s.id)'
                      f'{clause} ORDER BY s.as_of DESC, s.recorded_at DESC LIMIT 1',
                [as_of, cutoff_at, *params])


def list_snapshots(conn, *, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Snapshots newest recorded first; the real account's by default (`account_id=None` reads every account's)."""
    where, params = ('', []) if account_id is None else (' WHERE account_id = %s', [account_id])
    return _all(conn, f'SELECT {_SNAPSHOT_COLUMNS} FROM account_snapshots{where} '
                      'ORDER BY recorded_at DESC, created_at DESC', params)


def flow_by_request(conn, request_id):
    return _one(conn, f'SELECT {_FLOW_COLUMNS} FROM cash_flows WHERE request_id = %s', (request_id,))


def flow_by_id(conn, flow_id):
    return _one(conn, f'SELECT {_FLOW_COLUMNS} FROM cash_flows WHERE id = %s', (flow_id,))


def insert_flow(conn, rec: dict[str, Any], digest: str, *, recorded_at: datetime) -> dict[str, Any]:
    return _one(conn, 'INSERT INTO cash_flows (request_id, request_hash, flow_date, amount, note, recorded_at, '
                      f'account_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_FLOW_COLUMNS}',
                (rec['request_id'], digest, rec['flow_date'], rec['amount'], rec['note'], recorded_at,
                 rec.get('account_id') or REAL_ACCOUNT_ID))


def mark_flow_voided(conn, flow_id, reason: str, *, voided_at: datetime) -> dict[str, Any]:
    # Status fields only, as with trades; the flow itself is never rewritten.
    return _one(conn, f'UPDATE cash_flows SET voided_at = %s, void_reason = %s WHERE id = %s AND voided_at IS NULL '
                      f'RETURNING {_FLOW_COLUMNS}', (voided_at, reason, flow_id))


def list_flows(conn, *, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Flows oldest first; the real account's by default (`account_id=None` reads every account's)."""
    where, params = ('', []) if account_id is None else (' WHERE account_id = %s', [account_id])
    return _all(conn, f'SELECT {_FLOW_COLUMNS} FROM cash_flows{where} ORDER BY flow_date, created_at', params)


_EARNINGS_COLUMNS = 'id, ticker, earnings_date, status, source, fetched_at, created_at'


def store_earnings(conn, ticker: str, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Append only when the answer changed; a same-day re-fetch writes nothing."""
    latest = latest_earnings(conn, ticker, cutoff_at=now)
    if latest and latest['status'] == row['status'] and latest['earnings_date'] == row.get('earnings_date'):
        return {'status': 'unchanged', 'id': latest['id']}
    with conn.cursor() as cur:
        cur.execute('INSERT INTO earnings_dates (ticker, earnings_date, status, fetched_at) '
                    'VALUES (%s, %s, %s, %s) RETURNING id',
                    (ticker.upper(), row.get('earnings_date'), row['status'], now))
        return {'status': 'inserted', 'id': cur.fetchone()[0]}


def latest_earnings(conn, ticker: str, *, cutoff_at: datetime) -> dict[str, Any] | None:
    """The newest earnings answer known by ``cutoff_at``."""
    return _one(conn, f'SELECT {_EARNINGS_COLUMNS} FROM earnings_dates WHERE ticker = %s AND fetched_at <= %s '
                      'ORDER BY fetched_at DESC, created_at DESC LIMIT 1', (ticker.upper(), cutoff_at))


def earnings_row_by_id(conn, earnings_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_EARNINGS_COLUMNS} FROM earnings_dates WHERE id = %s', (earnings_id,))


_MONTHLY_COLUMNS = 'id, month, calc_version, stats, notes, supersedes_id, created_at'


def insert_monthly_review(conn, month: date, calc_version: str, stats: dict[str, Any], notes: str,
                          supersedes_id) -> dict[str, Any]:
    return _one(conn, 'INSERT INTO monthly_reviews (month, calc_version, stats, notes, supersedes_id) '
                      f'VALUES (%s, %s, %s::jsonb, %s, %s) RETURNING {_MONTHLY_COLUMNS}',
                (month, calc_version, _plan_json(stats), notes, supersedes_id))


def monthly_head(conn, month: date) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_MONTHLY_COLUMNS} FROM monthly_reviews m WHERE month = %s AND NOT EXISTS '
                      '(SELECT 1 FROM monthly_reviews s WHERE s.supersedes_id = m.id) '
                      'ORDER BY created_at DESC LIMIT 1', (month,))


def monthly_heads(conn, limit: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_MONTHLY_COLUMNS} FROM monthly_reviews m WHERE NOT EXISTS '
                      '(SELECT 1 FROM monthly_reviews s WHERE s.supersedes_id = m.id) '
                      'ORDER BY month DESC, created_at DESC LIMIT %s', (limit,))


_CANDIDATE_COLUMNS = 'id, ticker, signal_date, action, rule_id, rule_version, inputs, run_id, created_at'


def unit_candidates(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_CANDIDATE_COLUMNS} FROM signal_candidates WHERE ticker = %s AND signal_date = %s '
                      'ORDER BY created_at, rule_id', (ticker.upper(), signal_date))


def read_candidates(conn, ticker: str, *, since: date) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_CANDIDATE_COLUMNS} FROM signal_candidates WHERE ticker = %s AND signal_date >= %s '
                      'ORDER BY signal_date DESC, created_at, rule_id', (ticker.upper(), since))


def candidates_by_ids(conn, ids) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_CANDIDATE_COLUMNS} FROM signal_candidates WHERE id = ANY(%s)', (list(ids),))


def read_filings(conn, ticker: str, *, since: date, cutoff_at: datetime) -> list[dict[str, Any]]:
    """Filings indexed by ``cutoff_at`` (created_at is when this system first stored them)."""
    return _all(conn, 'SELECT id, ticker, form, filing_date, accession_number, primary_document, accepted_at, items '
                      'FROM filings WHERE ticker = %s AND filing_date >= %s AND created_at <= %s '
                      'ORDER BY filing_date, id', (ticker.upper(), since, cutoff_at))


WORKBENCH_TABLES = ('price_bars', 'news_items', 'news_item_tickers', 'signal_candidates', 'signal_briefs',
                    'signal_decisions', 'trades', 'weekly_reviews', 'filings', 'trade_plans', 'earnings_dates',
                    'account_snapshots', 'cash_flows', 'monthly_reviews', 'watchlist_items', 'pool_tiers',
                    'macro_observations', 'market_regimes', 'rule_backchecks', 'agent_runs', 'agent_tool_calls',
                    'review_pushes', 'screen_runs', 'screen_results')


def store_macro(conn, series_id: str, rows: list[dict[str, Any]], *, now: datetime) -> dict[str, int]:
    """Append observations; the same date with a different value appends a revision."""
    counts = {'inserted': 0, 'unchanged': 0}
    with conn.cursor() as cur:
        for row in rows:
            cur.execute('SELECT value FROM macro_observations '
                        'WHERE series_id = %s AND obs_date = %s ORDER BY fetched_at DESC LIMIT 1',
                        (series_id, row['obs_date']))
            latest = cur.fetchone()
            if latest is not None and latest[0] == row['value']:
                counts['unchanged'] += 1
                continue
            cur.execute('INSERT INTO macro_observations (series_id, obs_date, value, fetched_at) '
                        'VALUES (%s, %s, %s, %s)', (series_id, row['obs_date'], row['value'], now))
            counts['inserted'] += 1
    return counts


def read_macro(conn, series_id: str, *, end_date: date, cutoff_at: datetime, limit: int = 60) -> list[dict[str, Any]]:
    """Per obs_date the newest row fetched by the cutoff, oldest first, at most `limit` dates."""
    with conn.cursor() as cur:
        cur.execute('SELECT DISTINCT ON (obs_date) obs_date, value FROM macro_observations '
                    'WHERE series_id = %s AND obs_date <= %s AND fetched_at <= %s '
                    'ORDER BY obs_date DESC, fetched_at DESC LIMIT %s',
                    (series_id, end_date, cutoff_at, limit))
        rows = cur.fetchall()
    return [{'obs_date': r[0], 'value': r[1]} for r in reversed(rows)]


_REGIME_COLUMNS = 'id, session, calc_version, label, breadth, sectors, macro, inputs, created_at'


def _regime_fingerprint(snap: dict[str, Any]) -> str:
    compared = {k: snap[k] for k in ('label', 'breadth', 'sectors', 'macro')}
    return json.dumps(compared, sort_keys=True, separators=(',', ':'), default=str)


def store_regime(conn, snap: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Insert a snapshot; same content returns the stored row, different content is a conflict."""
    from investment_assistant.workbench.regime import REGIME_VERSION
    existing = _one(conn, f'SELECT {_REGIME_COLUMNS} FROM market_regimes '
                         'WHERE session = %s AND calc_version = %s', (snap['session'], snap['calc_version']))
    if existing is not None:
        stored = _regime_fingerprint(existing)
        return {'status': 'existing' if stored == _regime_fingerprint(snap) else 'conflict', 'record': existing}
    def _dumps(value: Any) -> str:
        return json.dumps(value, default=str)  # macro values arrive as Decimal

    placeholders = ', '.join(['%s'] * (7 if now is None else 8))
    params = [snap['session'], REGIME_VERSION, snap['label'], _dumps(snap['breadth']),
              _dumps(snap['sectors']), _dumps(snap['macro']), _dumps(snap['inputs'])]
    if now is not None:
        params.append(now)
    record = _one(conn, f'INSERT INTO market_regimes (session, calc_version, label, breadth, sectors, macro, inputs'
                        f'{", created_at" if now is not None else ""}) '
                        f'VALUES ({placeholders}) RETURNING {_REGIME_COLUMNS}', params)
    return {'status': 'inserted', 'record': record}


def regime_by_id(conn, regime_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_REGIME_COLUMNS} FROM market_regimes WHERE id = %s', (regime_id,))


def regime_for(conn, session: date, *, cutoff_at: datetime) -> dict[str, Any] | None:
    """The regime-1 snapshot of that session, as visible by the cutoff."""
    from investment_assistant.workbench.regime import REGIME_VERSION
    return _one(conn, f'SELECT {_REGIME_COLUMNS} FROM market_regimes '
                      'WHERE session = %s AND calc_version = %s AND created_at <= %s',
                (session, REGIME_VERSION, cutoff_at))


def latest_regime(conn, *, on_or_before: date, cutoff_at: datetime) -> dict[str, Any] | None:
    """The newest regime-1 snapshot at or before the date, visible by the cutoff."""
    from investment_assistant.workbench.regime import REGIME_VERSION
    return _one(conn, f'SELECT {_REGIME_COLUMNS} FROM market_regimes '
                      'WHERE session <= %s AND calc_version = %s AND created_at <= %s '
                      'ORDER BY session DESC, created_at DESC LIMIT 1',
                (on_or_before, REGIME_VERSION, cutoff_at))


_BACKCHECK_COLUMNS = 'id, calc_version, params, stats, created_at'


def insert_backcheck(conn, calc_version: str, params: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Freeze one backcheck run; rows are never rewritten."""
    return _one(conn, 'INSERT INTO rule_backchecks (calc_version, params, stats) '
                      f'VALUES (%s, %s::jsonb, %s::jsonb) RETURNING {_BACKCHECK_COLUMNS}',
                (calc_version, _canonical(params), _canonical(stats)))


def list_backchecks(conn, limit: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_BACKCHECK_COLUMNS} FROM rule_backchecks ORDER BY created_at DESC LIMIT %s',
                (limit,))


def pool_latest(conn) -> dict[str, dict[str, Any]]:
    """Each ticker's newest pool_tiers row: {'tier', 'sector_etf', 'source'}."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT DISTINCT ON (ticker) ticker, tier, sector_etf, source FROM pool_tiers '
                    'ORDER BY ticker, effective_session DESC, recorded_at DESC, id DESC')
        return {row['ticker']: {'tier': row['tier'], 'sector_etf': row['sector_etf'],
                                'source': row['source']}
                for row in cur.fetchall()}


def insert_pool_tiers(conn, rows: list[dict[str, Any]], *, session: date, now: datetime) -> int:
    """Append tier change rows; callers only pass rows that actually changed."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO pool_tiers (ticker, tier, sector_etf, effective_session, recorded_at, source) '
                        'VALUES (%s, %s, %s, %s, %s, %s)',
                        [(r['ticker'], r['tier'], r['sector_etf'], session, now,
                          r.get('source', 'watchlist')) for r in rows])
    return len(rows)


def pool_entry_at(conn, ticker: str, day: date, *, cutoff_at: datetime) -> dict[str, Any] | None:
    """The tier in force on `day` as known by `cutoff_at`; removed rows count too."""
    row = conn.execute('SELECT tier, sector_etf, source FROM pool_tiers '
                       'WHERE ticker = %s AND effective_session <= %s AND recorded_at <= %s '
                       'ORDER BY effective_session DESC, recorded_at DESC, id DESC LIMIT 1',
                       (ticker, day, cutoff_at)).fetchone()
    if row is None:
        return None
    return {'tier': row[0], 'sector_etf': row[1], 'source': row[2]}


# ---------------------------------------------------------------- daily screen (v2.2 §10)

_SCREEN_RUN_COLUMNS = ('id, session, calc_version, universe_name, universe_as_of, cutoff_at, status, summary, '
                       'run_id, created_at')


def insert_screen_run(conn, run: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Append one screen run and its per-ticker rows; same-session reruns add new rows.

    `run` may carry `created_at` (tests pin it to order runs inside one transaction);
    the default is the database clock."""
    record = _one(conn,
                  'INSERT INTO screen_runs (session, calc_version, universe_name, universe_as_of, cutoff_at, '
                  'status, summary, run_id, created_at) '
                  'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, COALESCE(%s, now())) '
                  f'RETURNING {_SCREEN_RUN_COLUMNS}',
                  (run['session'], run['calc_version'], run['universe_name'], run['universe_as_of'],
                   run['cutoff_at'], run['status'], _plan_json(run['summary']), run.get('run_id'),
                   run.get('created_at')))
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO screen_results (screen_run_id, ticker, screen_group, trigger_rules, filters, '
                        'rs, atr_pct, dollar_volume_20, rank, selected) '
                        'VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)',
                        [(record['id'], r['ticker'], r['group'], r.get('trigger_rules') or [],
                          _plan_json(r['filters']), r.get('rs'), r.get('atr_pct'),
                          r.get('dollar_volume_20'), r.get('rank'), r['selected']) for r in results])
    return record


def latest_screen_pick(conn, ticker: str, session: date, *,
                       cutoff_at: datetime | None = None) -> dict[str, Any] | None:
    """The ticker's newest selected row at or before `session`.

    `cutoff_at` is None inside the batch: the rule-plan stage must read the run the
    screen stage just wrote in this same pass, whose created_at is the database
    clock and would postdate the batch's own cutoff. Outside readers pass it."""
    sql = ('SELECT r.screen_run_id, r.screen_group, r.rs, r.rank FROM screen_results r '
           'JOIN screen_runs s ON s.id = r.screen_run_id '
           'WHERE r.ticker = %s AND r.selected AND s.session <= %s')
    params: list[Any] = [ticker.upper(), session]
    if cutoff_at is not None:
        sql += ' AND r.created_at <= %s'
        params.append(cutoff_at)
    row = conn.execute(sql + ' ORDER BY r.created_at DESC, r.id DESC LIMIT 1', params).fetchone()
    if row is None:
        return None
    return {'screen_run_id': row[0], 'group': row[1], 'rs': row[2], 'rank': row[3]}


def latest_screen_run(conn, *, session: date | None, cutoff_at: datetime | None = None) -> dict[str, Any] | None:
    """The newest run for one session, or overall.

    `cutoff_at` is None inside the batch (report and replay read the run the screen
    stage just wrote, whose database clock postdates the batch's own cutoff);
    time-point readers pass it as before."""
    where = 'WHERE session = %s' if session is not None else ''
    params: list[Any] = [session] if session is not None else []
    if cutoff_at is not None:
        where = (where + ' AND' if where else 'WHERE') + ' created_at <= %s'
        params.append(cutoff_at)
    return _one(conn, f'SELECT {_SCREEN_RUN_COLUMNS} FROM screen_runs {where} '
                      'ORDER BY created_at DESC, id DESC LIMIT 1', params)


def screen_results(conn, screen_run_id) -> list[dict[str, Any]]:
    """All rows of one run ordered by rank; the interface field is `group`."""
    return _all(conn, 'SELECT ticker, screen_group AS "group", trigger_rules, filters, rs, atr_pct, '
                      'dollar_volume_20, rank, selected FROM screen_results WHERE screen_run_id = %s '
                      'ORDER BY rank NULLS LAST, ticker', (screen_run_id,))


def screen_selected_since(conn, since: date, until: date, *,
                          cutoff_at: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Latest-run-per-session picks in [since, until]: {ticker: {'session', 'rank'}} of the newest pick.

    cutoff_at defaults to None for the batch's own reads (the session window already
    bounds what is visible); time-point readers pass it."""
    visible = ' AND s.created_at <= %(cutoff)s' if cutoff_at is not None else ''
    rows = conn.execute(
        'WITH latest AS ('
        '  SELECT DISTINCT ON (session) session, id FROM screen_runs s'
        '   WHERE s.session BETWEEN %(since)s AND %(until)s' + visible +
        '   ORDER BY s.session, s.created_at DESC, s.id DESC),'
        ' picked AS ('
        '  SELECT r.ticker, l.session, r.rank FROM screen_results r JOIN latest l ON r.screen_run_id = l.id'
        '   WHERE r.selected)'
        ' SELECT DISTINCT ON (ticker) ticker, session, rank FROM picked'
        '  ORDER BY ticker, session DESC',
        {'since': since, 'until': until, 'cutoff': cutoff_at}).fetchall()
    return {row[0]: {'session': row[1], 'rank': row[2]} for row in rows}


def locked_screen_tickers(conn, session: date, *, cutoff_at: datetime) -> set[str]:
    """Valid plans for the session plus tickers holding an open position from unvoided trades."""
    tickers = {row[0] for row in conn.execute(
        'SELECT DISTINCT ticker FROM trade_plans '
        "WHERE kind IN ('rule', 'own') AND status = 'ok' AND valid_until >= %s AND created_at <= %s",
        (session, cutoff_at)).fetchall()}
    tickers |= open_position_tickers(conn)
    return tickers


def screen_lockable_tickers(conn) -> set[str]:
    """Tickers a lock may keep as screen members: still in the pool by their newest row and
    screen-sourced at some point (a screen member now, or a pick since flipped to the watchlist).

    Off-pool holdings and plans of tickers that never came from the screen stay out."""
    rows = conn.execute(
        'SELECT ticker FROM (SELECT DISTINCT ON (ticker) ticker, tier FROM pool_tiers '
        '  ORDER BY ticker, effective_session DESC, recorded_at DESC, id DESC) latest '
        "WHERE tier <> 'removed' AND EXISTS "
        "  (SELECT 1 FROM pool_tiers p WHERE p.ticker = latest.ticker AND p.source = 'screen')").fetchall()
    return {row[0] for row in rows}


def open_position_tickers(conn, *, account_id=REAL_ACCOUNT_ID) -> set[str]:
    """Tickers the account still holds a position in, from its unvoided trades (the real account's by default)."""
    from investment_assistant.workbench import positions

    held: set[str] = set()
    for ticker in sorted({t['ticker'] for t in list_trades(conn, account_id=account_id)
                          if t['voided_at'] is None}):
        try:
            book = positions.build(list_trades(conn, ticker, account_id=account_id),
                                   splits=split_events(conn, ticker))
        except positions.PositionError:
            continue  # a broken trade history must not fail the whole screen
        if book['position'] is not None:
            held.add(ticker)
    return held


# ---------------------------------------------------------------- fetch runs (v2.3 §5)

_FETCH_RUN_COLUMNS = ('id, family, job, trigger, run_id, scope, ticker, session, status, target_count, '
                      'ok_count, failures, detail, started_at, finished_at, created_at')


def insert_fetch_run(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one fetch-run row; `failures`/`detail` arrive as Python objects, JSON-encoded here.

    `row` keys are the table columns except `id`/`created_at`, which the database sets."""
    return _one(conn,
                'INSERT INTO data_fetch_runs (family, job, trigger, run_id, scope, ticker, session, status, '
                'target_count, ok_count, failures, detail, started_at, finished_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) '
                f'RETURNING {_FETCH_RUN_COLUMNS}',
                (row['family'], row['job'], row['trigger'], row.get('run_id'), row['scope'], row.get('ticker'),
                 row.get('session'), row['status'], row['target_count'], row['ok_count'],
                 _plan_json(row.get('failures') or []), _plan_json(row.get('detail') or {}),
                 row['started_at'], row['finished_at']))


def fetch_runs_ready(conn) -> bool:
    """True when migration 024 has run; the workbench itself does not require this table."""
    return conn.execute("SELECT to_regclass('data_fetch_runs')").fetchone()[0] is not None


def latest_fetch_runs(conn, *, scope: str | None = None) -> dict[str, dict[str, Any]]:
    """The newest run per family: {family: row}; ``scope`` ('family' / 'ticker') narrows it."""
    where, params = ('WHERE scope = %s ', [scope]) if scope is not None else ('', [])
    return {row['family']: row for row in _all(
        conn, f'SELECT DISTINCT ON (family) {_FETCH_RUN_COLUMNS} FROM data_fetch_runs {where}'
              'ORDER BY family, created_at DESC, id DESC', params)}


def fetch_runs(conn, family: str, limit: int = 20) -> list[dict[str, Any]]:
    """One family's runs, newest first."""
    return _all(conn, f'SELECT {_FETCH_RUN_COLUMNS} FROM data_fetch_runs WHERE family = %s '
                      'ORDER BY created_at DESC, id DESC LIMIT %s', (family, limit))


def last_success_at(conn, *, scope: str | None = None) -> dict[str, datetime]:
    """Per family, the finished_at of the newest run that succeeded (status success or partial);
    ``scope`` ('family' / 'ticker') narrows it."""
    where, params = ('AND scope = %s ', [scope]) if scope is not None else ('', [])
    rows = conn.execute("SELECT DISTINCT ON (family) family, finished_at FROM data_fetch_runs "
                        f"WHERE status IN ('success', 'partial') {where}"
                        'ORDER BY family, finished_at DESC, id DESC', params).fetchall()
    return {row[0]: row[1] for row in rows}


# --- stage runs (v2.4 §5) ---

_STAGE_RUN_COLUMNS = ('id, run_id, job, trigger, session, stage, stages_version, status, reason, input_count, '
                      'output_count, skips, failures, degradations, outputs, detail, started_at, finished_at, '
                      'duration_ms, created_at')


def insert_stage_run(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one stage-run row; the five JSONB columns arrive as Python objects, encoded here.

    `row` keys are the table columns except `id`/`created_at`, which the database sets. `job` is written as given
    ('workbench' or, since migration 028, 'daily'). `attempt` (default 1) is written, and returned, for every
    `daily` row and for any attempt above 1; a v2.7 `workbench` first attempt omits the column so it still lands
    in a database that has not run migration 028 (the column default is 1)."""
    attempt = int(row.get('attempt') or 1)
    with_attempt = attempt != 1 or row['job'] == 'daily'
    columns = ', '.join(['run_id', 'job', 'trigger', 'session', 'stage', 'stages_version', 'status', 'reason',
                         'input_count', 'output_count', 'skips', 'failures', 'degradations', 'outputs', 'detail',
                         'started_at', 'finished_at', 'duration_ms'] + (['attempt'] if with_attempt else []))
    marks = ('%s, ' * 10 + '%s::jsonb, ' * 5 + '%s, %s, %s') + (', %s' if with_attempt else '')
    return _one(conn,
                f'INSERT INTO stage_runs ({columns}) VALUES ({marks}) '
                f"RETURNING {_STAGE_RUN_COLUMNS}{', attempt' if with_attempt else ''}",
                (row['run_id'], row['job'], row['trigger'], row.get('session'), row['stage'],
                 row['stages_version'], row['status'], row.get('reason'), row['input_count'],
                 row['output_count'], _plan_json(row.get('skips') or []),
                 _plan_json(row.get('failures') or []), _plan_json(row.get('degradations') or []),
                 _plan_json(row.get('outputs') or {}), _plan_json(row.get('detail') or {}),
                 row['started_at'], row['finished_at'], row['duration_ms'],
                 *([attempt] if with_attempt else [])))


def stage_runs_ready(conn) -> bool:
    """True when migration 025 has run; the workbench itself does not require this table."""
    return conn.execute("SELECT to_regclass('stage_runs')").fetchone()[0] is not None


def stage_runs_for_run(conn, run_id: str) -> list[dict[str, Any]]:
    """One run's stage rows, oldest first."""
    return _all(conn, f'SELECT {_STAGE_RUN_COLUMNS} FROM stage_runs WHERE run_id = %s ORDER BY created_at, id',
                (run_id,))


# --- daily workflow (v2.8, migration 028) ---

# Stage order per stages_version, for sorting rows of runs booked under different versions (literal on purpose:
# `stages` imports this module).
_STAGE_ORDER = {
    'stages-1': ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'push'),
    'stages-2': ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push'),
    'stages-3': ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'retro', 'push'),
    'stages-4': ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push',
                 'retro'),
}
_ATTEMPT_RESET_COLUMNS = 'id, ticker, signal_date, mode, request_id, origin, created_at'


def daily_ready(conn) -> bool:
    """True when migration 028 has run: `stage_runs.attempt`, the attempt-reset table and the `daily` job row.

    028 creates the credential tables in the same transaction, so the attempt-reset table stands in for them (this
    module never touches the connection tables, spec §10.2). A pure existence check (no row is written); `daily` refuses to run without it (D36)."""
    present = conn.execute("SELECT to_regclass('stage_runs'), to_regclass('agent_attempt_resets'), "
                           "to_regclass('scheduled_jobs')").fetchone()
    if any(item is None for item in present):
        return False
    has_attempt = conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = 'stage_runs'::regclass "
                               "AND attname = 'attempt' AND NOT attisdropped").fetchone()
    has_job = conn.execute("SELECT 1 FROM scheduled_jobs WHERE name = 'daily'").fetchone()
    return bool(has_attempt and has_job)


def stage_rows_for_run(conn, run_id: str) -> list[dict[str, Any]]:
    """One run's stage rows including `attempt`, ordered by the stage's position in its stages_version, then
    attempt (oldest first). Needs migration 028."""
    rows = _all(conn, f'SELECT {_STAGE_RUN_COLUMNS}, attempt FROM stage_runs WHERE run_id = %s ORDER BY id',
                (run_id,))

    def position(row: dict[str, Any]) -> tuple[int, int, int]:
        order = _STAGE_ORDER.get(row['stages_version'], ())
        index = order.index(row['stage']) if row['stage'] in order else len(order)
        return index, row['attempt'], row['id']

    return sorted(rows, key=position)


def max_stage_attempt(conn, run_id: str) -> int:
    """The highest `attempt` booked for the run; 0 when it has no rows. Needs migration 028."""
    return int(conn.execute('SELECT COALESCE(max(attempt), 0) FROM stage_runs WHERE run_id = %s',
                            (run_id,)).fetchone()[0])


def insert_attempt_reset(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Append one attempt-reset record (D26); a repeated `request_id` returns the stored row with `replayed`.

    `row` keys: ticker, signal_date, mode ('propose' / 'interpret'), request_id, origin ('run_detail' /
    'plan_page' / 'api')."""
    inserted = _one(conn,
                    'INSERT INTO agent_attempt_resets (ticker, signal_date, mode, request_id, origin) '
                    'VALUES (%s, %s, %s, %s, %s) ON CONFLICT (request_id) DO NOTHING '
                    f'RETURNING {_ATTEMPT_RESET_COLUMNS}',
                    (row['ticker'], row['signal_date'], row['mode'], row['request_id'], row['origin']))
    if inserted is not None:
        return {**inserted, 'replayed': False}
    existing = _one(conn, f'SELECT {_ATTEMPT_RESET_COLUMNS} FROM agent_attempt_resets WHERE request_id = %s',
                    (row['request_id'],))
    return {**existing, 'replayed': True}


def attempt_resets_for(conn, ticker: str, signal_date: date, mode: str) -> list[dict[str, Any]]:
    """A decision unit's reset records for one mode, newest first."""
    return _all(conn, f'SELECT {_ATTEMPT_RESET_COLUMNS} FROM agent_attempt_resets '
                      'WHERE ticker = %s AND signal_date = %s AND mode = %s ORDER BY created_at DESC, id DESC',
                (ticker, signal_date, mode))


def latest_daily_push_finished_at(conn, *, before: datetime) -> datetime | None:
    """When the latest `daily` push that actually pushed finished before `before` (the base of the review-card
    count in the plan summary, D3); None when there was none. Skipped and failed pushes do not count, nor does the
    holiday line of a closed market (`detail.closed`): it would move the base past the cards of the last trading day."""
    return conn.execute("SELECT max(finished_at) FROM stage_runs WHERE job = 'daily' AND stage = 'push' "
                        "AND status IN ('success', 'partial', 'degraded') AND finished_at < %s "
                        "AND NOT coalesce((detail->>'closed')::boolean, false)",
                        (before,)).fetchone()[0]


# --- run detail reads (v2.8 §7.2): lean columns, ids only from the stage evidence, nothing written ---

def job_reports_for_run(conn, run_id: str) -> list[dict[str, Any]]:
    """Every `job_reports` row of one run id (a first execution and its resumes), oldest first."""
    return _all(conn, 'SELECT id, task, run_id, status, trigger_source, started_at, finished_at, summary, '
                      'target_start_date FROM job_reports WHERE run_id = %s ORDER BY started_at, id', (run_id,))


def last_connection_notice_at(conn) -> datetime | None:
    """When the `daily` execution that last *sent* the connection-lost notice checked the connection
    (`summary.connection_notice.checked_at`); None when no execution did. The base of the once-per-episode notice
    (v2.8 §8.8): a failure at or after it has not been told yet."""
    row = conn.execute("SELECT summary->'connection_notice'->>'checked_at' FROM job_reports WHERE task = 'daily' "
                       "AND summary->'connection_notice'->>'sent' = 'true' "
                       "AND summary->'connection_notice' ? 'checked_at' ORDER BY started_at DESC, id DESC "
                       'LIMIT 1').fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def fetch_runs_by_ids(conn, ids) -> list[dict[str, Any]]:
    """The `data_fetch_runs` rows a stage row refers to, in the order of `ids`."""
    rows = _all(conn, f'SELECT {_FETCH_RUN_COLUMNS} FROM data_fetch_runs WHERE id = ANY(%s)', (list(ids),))
    order = {int(i): n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda row: order.get(int(row['id']), len(order)))


def plans_by_ids(conn, ids) -> list[dict[str, Any]]:
    return _all(conn, 'SELECT id, ticker, signal_date, kind, status, invalid_reasons FROM trade_plans '
                      'WHERE id = ANY(%s::uuid[]) ORDER BY ticker, signal_date, created_at', (list(map(str, ids)),))


def candidate_refs_by_ids(conn, ids) -> list[dict[str, Any]]:
    return _all(conn, 'SELECT id, ticker, signal_date, rule_id FROM signal_candidates WHERE id = ANY(%s::uuid[])',
                (list(map(str, ids)),))


def agent_runs_by_ids(conn, ids) -> list[dict[str, Any]]:
    """Run rows without the output / counter payloads (the detail page lists them, the reasoning view opens one)."""
    return _all(conn, 'SELECT id, ticker, signal_date, mode, status, reason, attempt, model, plan_id, input_tokens, '
                      'output_tokens, created_at FROM agent_runs WHERE id = ANY(%s::uuid[]) '
                      'ORDER BY ticker, signal_date, mode, created_at', (list(map(str, ids)),))


def plan_reviews_by_ids(conn, ids) -> list[dict[str, Any]]:
    """Needs migration 026."""
    return _all(conn, 'SELECT id, ticker, signal_date, outcome, head_plan_id, new_plan_id FROM plan_reviews '
                      'WHERE id = ANY(%s) ORDER BY ticker, signal_date, id', (list(ids),))


def review_card_refs_by_ids(conn, ids) -> list[dict[str, Any]]:
    """Needs migration 027."""
    return _all(conn, 'SELECT id, ticker, subject, end_kind FROM review_cards WHERE id = ANY(%s) ORDER BY id',
                (list(ids),))


def stage_runs_for_session(conn, session: date) -> list[dict[str, Any]]:
    """One session's stage rows across runs, oldest first."""
    return _all(conn, f'SELECT {_STAGE_RUN_COLUMNS} FROM stage_runs WHERE session = %s ORDER BY created_at, id',
                (session,))


def stage_run_for_plan(conn, plan_id: str) -> dict[str, Any] | None:
    """The earliest plan/agent stage row that inserted the plan (agent rows via outputs.plan_ids).

    Not the candidate's run_id: a candidate may have been written by an earlier run."""
    return _one(conn, f'SELECT {_STAGE_RUN_COLUMNS} FROM stage_runs '
                      "WHERE stage IN ('plan', 'agent') "
                      "AND (outputs->'inserted' ? %s OR outputs->'plan_ids' ? %s) "
                      'ORDER BY created_at, id LIMIT 1', (plan_id, plan_id))


def workbench_reports_between(conn, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """workbench run reports with started_at in [start, end), oldest first."""
    return _all(conn, "SELECT run_id, started_at, trigger_source FROM job_reports "
                      "WHERE task IN ('workbench', 'daily') AND started_at >= %s AND started_at < %s "
                      'ORDER BY started_at, id', (start, end))


def workbench_run_ids(conn, run_ids: list[str]) -> set[str]:
    """The given run ids that have a workbench report in job_reports."""
    if not run_ids:
        return set()
    rows = _all(conn, "SELECT run_id FROM job_reports WHERE task IN ('workbench', 'daily') AND run_id = ANY(%s)",
                (list(run_ids),))
    return {row['run_id'] for row in rows}


def last_push(conn, *, session: date | None = None) -> dict[str, Any] | None:
    """The newest recorded push result for the workbench task, optionally for one session."""
    if conn.execute("SELECT to_regclass('job_reports')").fetchone()[0] is None:
        return None
    sql = "SELECT summary->'push' AS push FROM job_reports WHERE task IN ('workbench', 'daily') AND summary ? 'push'"
    params: list[Any] = []
    if session is not None:
        sql += " AND (summary->'push'->>'session') = %s"
        params.append(session.isoformat())
    sql += ' ORDER BY created_at DESC LIMIT 1'
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else None


def last_successful_push_at(conn, *, before_session: date) -> datetime | None:
    """Where the newest successful daily push of a session earlier than `before_session` stopped reading
    reviews (v2.5 D25): its `summary.push.reviews_until`, or the report's created_at for pushes recorded
    before that key existed. A New York holiday's one-line message (summary.status = skipped) is not a
    daily push and does not move the window."""
    if conn.execute("SELECT to_regclass('job_reports')").fetchone()[0] is None:
        return None
    row = conn.execute(
        "SELECT max(COALESCE((summary->'push'->>'reviews_until')::timestamptz, created_at)) "
        "FROM job_reports WHERE task IN ('workbench', 'daily') "
        "AND summary->'push'->'sent' = 'true'::jsonb AND (summary->'push'->>'session') < %s "
        "AND (summary->>'status') IS DISTINCT FROM 'skipped'", (before_session.isoformat(),)).fetchone()
    return row[0] if row else None


def missing_tables(conn) -> list[str]:
    row = conn.execute('SELECT ' + ', '.join(f"to_regclass('{t}')" for t in WORKBENCH_TABLES)).fetchone()
    missing = [t for t, found in zip(WORKBENCH_TABLES, row) if found is None]
    if 'filings' not in missing and conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'filings' AND column_name = 'accepted_at' "
            "AND table_schema = ANY(current_schemas(false))").fetchone() is None:
        missing.append('filings.accepted_at')
    return missing


def fill_filing_acceptance(conn, metas: list[dict[str, Any]]) -> int:
    """Copy SEC's acceptance time and items onto indexed filings that lack them; never overwrite."""
    filled = 0
    for meta in metas:
        accession, accepted = meta.get('accession_number'), meta.get('accepted_at')
        if not accession or (accepted is None and meta.get('items') is None):
            continue
        filled += conn.execute(
            'UPDATE filings SET accepted_at = COALESCE(accepted_at, %s), items = COALESCE(items, %s) '
            'WHERE accession_number = %s AND ((accepted_at IS NULL AND %s::timestamptz IS NOT NULL) '
            'OR (items IS NULL AND %s::text[] IS NOT NULL))',
            (accepted, meta.get('items'), accession, accepted, meta.get('items'))).rowcount
    return filled


# ---------------------------------------------------------------- agent track (P3 §3)

_AGENT_RUN_COLUMNS = ('id, ticker, signal_date, pool, attempt, mode, agent_version, provider, model, cutoff_at, '
                      'status, reason, detail, output, counter, plan_id, input_tokens, output_tokens, '
                      'started_at, finished_at, created_at')


def insert_agent_run(conn, run: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The run row and all its tool calls, in the caller's transaction (§3.3 order:
    plan, run, calls; the plan is inserted by the service before this call)."""
    record = _one(conn,
                  'INSERT INTO agent_runs (id, ticker, signal_date, pool, attempt, mode, agent_version, provider, '
                  'model, cutoff_at, status, reason, detail, output, counter, plan_id, input_tokens, output_tokens, '
                  'started_at, finished_at) '
                  'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s) '
                  f'RETURNING {_AGENT_RUN_COLUMNS}',
                  (run['id'], run['ticker'], run['signal_date'], run['pool'], run['attempt'],
                   run.get('mode', 'propose'), run['agent_version'], run['provider'], run['model'], run['cutoff_at'], run['status'],
                   run['reason'], run.get('detail'),
                   _plan_json(run['output']) if run.get('output') is not None else None,
                   _plan_json(run['counter']) if run.get('counter') is not None else None,
                   run.get('plan_id'), run['input_tokens'], run['output_tokens'],
                   run['started_at'], run['finished_at']))
    for c in calls:
        conn.execute('INSERT INTO agent_tool_calls (run_id, seq, phase, tool, args, result, result_sha256, '
                     'called_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)',
                     (run['id'], c['seq'], c['phase'], c['tool'], _plan_json(c['args']),
                      _plan_json(c['result']), c['result_sha256'], c['called_at']))
    return record


def agent_runs_for(conn, ticker: str, signal_date: date, mode: str = 'propose') -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_AGENT_RUN_COLUMNS} FROM agent_runs WHERE ticker = %s AND signal_date = %s '
                      'AND mode = %s ORDER BY created_at, id', (ticker.upper(), signal_date, mode))


def latest_agent_run(conn, ticker: str, signal_date: date, mode: str = 'propose') -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_AGENT_RUN_COLUMNS} FROM agent_runs WHERE ticker = %s AND signal_date = %s '
                      'AND mode = %s ORDER BY created_at DESC, id DESC LIMIT 1',
                (ticker.upper(), signal_date, mode))


def agent_run_by_id(conn, run_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_AGENT_RUN_COLUMNS} FROM agent_runs WHERE id = %s', (run_id,))


def agent_tool_calls(conn, run_id) -> list[dict[str, Any]]:
    return _all(conn, 'SELECT id, run_id, seq, phase, tool, args, result, result_sha256, called_at '
                      'FROM agent_tool_calls WHERE run_id = %s ORDER BY seq', (run_id,))


def list_agent_runs(conn, since: date) -> list[dict[str, Any]]:
    """Run rows for the API list view: no result payloads, but a tool-call count each."""
    return _all(conn, 'SELECT r.id, r.ticker, r.signal_date, r.pool, r.attempt, r.mode, r.agent_version, r.provider, '
                      'r.model, r.cutoff_at, r.status, r.reason, r.detail, r.plan_id, r.input_tokens, r.output_tokens, '
                      'r.started_at, r.finished_at, r.created_at, '
                      '(SELECT count(*) FROM agent_tool_calls c WHERE c.run_id = r.id) AS tool_calls '
                      'FROM agent_runs r WHERE r.signal_date >= %s ORDER BY r.created_at, r.id', (since,))


def track_units(conn, since: date, until: date) -> list[dict[str, Any]]:
    """§4.1: units with a rule plan or a propose run in the window (union),
    each pool membership decided later; ordered by (signal_date, ticker)."""
    return _all(conn,
                'SELECT u.ticker, u.signal_date FROM ('
                "SELECT ticker, signal_date FROM trade_plans WHERE kind = 'rule' AND signal_date BETWEEN %s AND %s "
                'UNION '
                "SELECT ticker, signal_date FROM agent_runs WHERE mode = 'propose' AND signal_date BETWEEN %s AND %s"
                ') u ORDER BY u.signal_date, u.ticker',
                (since, until, since, until))


def agent_tokens_on(conn, ny_date: date) -> int:
    """Tokens of runs whose started_at falls on that New York date (§4 daily cap)."""
    row = conn.execute("SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM agent_runs "
                       "WHERE (started_at AT TIME ZONE 'America/New_York')::date = %s", (ny_date,)).fetchone()
    return int(row[0] or 0)


def agent_plan(conn, ticker: str, signal_date: date) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_PLAN_COLUMNS} FROM trade_plans WHERE ticker = %s AND signal_date = %s '
                      "AND kind = 'agent' ORDER BY created_at DESC LIMIT 1", (ticker.upper(), signal_date))


def candidate_groups_since(conn, since: date) -> list[dict[str, Any]]:
    """open_candidate_groups' shape, but decided units included (agent coverage needs them all)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            'SELECT c.id, c.ticker, c.signal_date, c.action, c.rule_id, c.rule_version, c.inputs, c.created_at '
            'FROM signal_candidates c WHERE c.signal_date >= %s '
            'ORDER BY c.signal_date DESC, c.ticker, c.created_at, c.rule_id', (since,))
        rows = cur.fetchall()
    groups: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (row['ticker'], row['signal_date'])
        groups.setdefault(key, {'ticker': key[0], 'signal_date': key[1], 'candidates': []})['candidates'].append(row)
    return list(groups.values())


# --- plan timeline (v2.5 §6): agent_plan_versions, plan_reviews, user_inputs ---
# Append-only tables from migration 026; deliberately not in WORKBENCH_TABLES (§6.5): an
# unmigrated database degrades through timeline_ready() instead of failing the workbench.

EFFECTIVE_REVIEW_OUTCOMES = ('maintain', 'revise', 'invalidate')

_VERSION_COLUMNS = ('plan_id, ticker, signal_date, version_no, supersedes_plan_id, origin, agent_run_id, '
                    'cutoff_at, as_of_session, diff, schema_version, created_at')
_REVIEW_COLUMNS = ('id, ticker, signal_date, head_plan_id, session, trigger, run_id, mode, timeline_version, '
                   'signals, conditions, user_input_ids, outcome, decided_by, reason, refs, review_agent_run_id, '
                   'revision_agent_run_id, new_plan_id, detail, attempt, cutoff_at, input_tokens, output_tokens, '
                   'created_at')
_USER_INPUT_COLUMNS = ('id, request_id, request_hash, ticker, signal_date, kind, body, source_url, supersedes_id, '
                       'created_at')


def timeline_ready(conn) -> bool:
    """True when migration 026 has run: the three tables and signal_decisions.agent_plan_id exist.

    Resolved through the search_path like every other query here, and never raises on a missing
    table, so the caller's transaction stays usable."""
    row = conn.execute(
        "SELECT to_regclass('agent_plan_versions') IS NOT NULL AND to_regclass('plan_reviews') IS NOT NULL "
        "AND to_regclass('user_inputs') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_attribute "
        "WHERE attrelid = to_regclass('signal_decisions') AND attname = 'agent_plan_id' AND NOT attisdropped)"
    ).fetchone()
    return bool(row[0])


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

def insert_review_push(conn, row: dict[str, Any]) -> dict[str, Any]:
    """One row per push attempt, successful or not; append-only."""
    return _one(conn, 'INSERT INTO review_pushes (kind, period, review_id, trigger_source, sent, messages, reason) '
                      'VALUES (%s, %s, %s, %s, %s, %s, %s) '
                      'RETURNING id, kind, period, review_id, trigger_source, sent, messages, reason, created_at',
                (row['kind'], row['period'], row.get('review_id'), row['trigger_source'],
                 row['sent'], row.get('messages') or 0, row.get('reason')))


def review_push_sent(conn, kind: str, period: date) -> bool:
    """Whether this kind and period already has a fully successful push (§6.1 idempotence)."""
    return conn.execute('SELECT EXISTS (SELECT 1 FROM review_pushes WHERE kind = %s AND period = %s '
                        'AND sent = true)', (kind, period)).fetchone()[0]


def list_review_pushes(conn, limit: int) -> list[dict[str, Any]]:
    return _all(conn, 'SELECT id, kind, period, review_id, trigger_source, sent, messages, reason, created_at '
                      'FROM review_pushes ORDER BY created_at DESC, id DESC LIMIT %s', (limit,))


def workbench_started_on(conn) -> date | None:
    """The first day the workbench was in use: the earliest decision (New York date)
    or account snapshot as_of, whichever comes first; None while neither exists."""
    row = conn.execute(
        "SELECT least((SELECT min((decided_at AT TIME ZONE 'America/New_York')::date) FROM signal_decisions), "
        '(SELECT min(as_of) FROM account_snapshots))').fetchone()
    return row[0] if row else None


def latest_account_record_at(conn) -> datetime | None:
    """The latest moment account inputs were recorded or corrected: snapshots and flows."""
    row = conn.execute(
        'SELECT greatest(s.rec, f.rec, f.void) FROM '
        '(SELECT max(recorded_at) AS rec FROM account_snapshots) s, '
        '(SELECT max(recorded_at) AS rec, max(voided_at) AS void FROM cash_flows) f').fetchone()
    return row[0] if row else None

# ---------------------------------------------------------------- coverage reads (v2.3 §8)


def latest_bar_dates(conn, tickers, *, cutoff_at: datetime) -> dict[str, date]:
    """Per ticker the newest bar date any revision of which was known by ``cutoff_at``."""
    if not tickers:
        return {}
    rows = _all(conn, 'SELECT ticker, max(price_date) AS latest FROM price_bars '
                      'WHERE ticker = ANY(%s) AND fetched_at <= %s GROUP BY ticker',
                (list(tickers), cutoff_at))
    return {row['ticker']: row['latest'] for row in rows}


def bar_counts(conn, tickers, *, end_date: date, cutoff_at: datetime) -> dict[str, int]:
    """Per ticker the distinct bar dates up to ``end_date`` known by ``cutoff_at``; revisions count once."""
    if not tickers:
        return {}
    rows = _all(conn, 'SELECT ticker, count(DISTINCT price_date) AS n FROM price_bars '
                      'WHERE ticker = ANY(%s) AND price_date <= %s AND fetched_at <= %s GROUP BY ticker',
                (list(tickers), end_date, cutoff_at))
    return {row['ticker']: row['n'] for row in rows}


def latest_news_at(conn, tickers, *, cutoff_at: datetime) -> dict[str, datetime]:
    """Per ticker the newest COALESCE(published_at, fetched_at) visible to read_news at ``cutoff_at``."""
    if not tickers:
        return {}
    rows = _all(conn, 'SELECT t.ticker, max(COALESCE(n.published_at, n.fetched_at)) AS latest '
                      'FROM news_items n JOIN news_item_tickers t ON t.news_item_id = n.id '
                      'WHERE t.ticker = ANY(%s) AND n.fetched_at <= %s '
                      'AND (n.published_at IS NULL OR n.published_at <= %s) '
                      'GROUP BY t.ticker', (list(tickers), cutoff_at, cutoff_at))
    return {row['ticker']: row['latest'] for row in rows}


def latest_filing_dates(conn, tickers, *, cutoff_at: datetime) -> dict[str, date]:
    """Per ticker the greater of the newest filing_date and the newest sec_8k news date.

    Both sources are checked as known by ``cutoff_at`` (filings.created_at, news
    fetched_at); the news date is the New York calendar date of its published_at
    (EDGAR acceptance time). sec_8k rows without a published_at have no filing-date
    basis and are not counted — the fetch date must not masquerade as a filing
    date; better undercounted than faked (I14)."""
    if not tickers:
        return {}
    rows = _all(conn, 'SELECT ticker, max(day) AS latest FROM ('
                      '  SELECT ticker, max(filing_date) AS day FROM filings '
                      '  WHERE ticker = ANY(%s) AND created_at <= %s GROUP BY ticker '
                      '  UNION ALL '
                      '  SELECT t.ticker, (max(n.published_at) '
                      "                    AT TIME ZONE 'America/New_York')::date AS day "
                      '  FROM news_items n JOIN news_item_tickers t ON t.news_item_id = n.id '
                      "  WHERE t.ticker = ANY(%s) AND n.source = 'sec_8k' AND n.fetched_at <= %s "
                      '  AND n.published_at IS NOT NULL AND n.published_at <= %s '
                      '  GROUP BY t.ticker) days GROUP BY ticker',
                (list(tickers), cutoff_at, list(tickers), cutoff_at, cutoff_at))
    return {row['ticker']: row['latest'] for row in rows}


def latest_macro_dates(conn, series_ids, *, cutoff_at: datetime) -> dict[str, date]:
    """Per series the newest observation date any revision of which was known by ``cutoff_at``."""
    if not series_ids:
        return {}
    rows = _all(conn, 'SELECT series_id, max(obs_date) AS latest FROM macro_observations '
                      'WHERE series_id = ANY(%s) AND fetched_at <= %s GROUP BY series_id',
                (list(series_ids), cutoff_at))
    return {row['series_id']: row['latest'] for row in rows}


def bar_revision_counts(conn, ticker: str, *, since: date, cutoff_at: datetime) -> dict[date, int]:
    """Per price_date from ``since`` on, how many revisions were known by ``cutoff_at``."""
    rows = conn.execute('SELECT price_date, count(*) FROM price_bars '
                        'WHERE ticker = %s AND price_date >= %s AND fetched_at <= %s '
                        'GROUP BY price_date', (ticker.upper(), since, cutoff_at)).fetchall()
    return {row[0]: row[1] for row in rows}


def macro_revision_counts(conn, series_id: str, *, cutoff_at: datetime) -> dict[date, int]:
    """Per obs_date how many revisions of the series were known by ``cutoff_at``."""
    rows = conn.execute('SELECT obs_date, count(*) FROM macro_observations '
                        'WHERE series_id = %s AND fetched_at <= %s GROUP BY obs_date',
                        (series_id, cutoff_at)).fetchall()
    return {row[0]: row[1] for row in rows}


# --- growth loop (v2.7 §11): review cards, my knowledge, citations, discussions ---
# Eleven append-only tables from migration 027, deliberately not in WORKBENCH_TABLES (spec D29): an
# unmigrated database degrades through growth_ready(). This layer only INSERTs into and SELECTs from them
# (spec D30; tests/test_migration_027.py reads this file's source to hold it to that).

GROWTH_TABLES = ('review_cards', 'review_card_drafts', 'discussions', 'knowledge_items', 'discussion_turns',
                 'knowledge_drafts', 'knowledge_versions', 'knowledge_status', 'knowledge_draft_verdicts',
                 'review_card_verdicts', 'knowledge_citations')

_CARD_COLUMNS = ('id, event_key, subject, ticker, signal_date, buy_trade_id, end_kind, end_session, '
                 'retro_version, facts, detected_run_id, created_at')
_CARD_DRAFT_COLUMNS = 'id, card_id, attempt, trigger, agent_run_id, status, reason, output, created_at'
_CARD_VERDICT_COLUMNS = 'id, card_id, request_id, request_hash, draft_id, note, created_at'
_KNOWLEDGE_ITEM_COLUMNS = 'id, origin, source_card_id, source_discussion_id, created_at'
_KNOWLEDGE_VERSION_COLUMNS = ('id, knowledge_id, version, supersedes_id, request_id, request_hash, draft_id, title, '
                              'body, scope, counter_examples, concepts, tickers, schema_version, created_at')
_KNOWLEDGE_STATUS_COLUMNS = 'id, knowledge_id, status, reason, request_id, request_hash, created_at'
_KNOWLEDGE_DRAFT_COLUMNS = ('id, origin, card_draft_id, lesson_index, discussion_turn_id, target_knowledge_id, '
                            'target_version, title, body, scope, counter_examples, concepts, tickers, refs, '
                            'created_at')
_CITATION_COLUMNS = 'id, agent_run_id, plan_id, review_id, knowledge_id, version, field, call_seq, created_at'
_DISCUSSION_COLUMNS = ('id, request_id, request_hash, context_kind, ticker, signal_date, plan_id, card_id, '
                       'created_at')
_TURN_COLUMNS = ('id, discussion_id, seq, role, request_id, request_hash, body, attachment, run_id, reply_to, '
                 'status, reason, refs, detail, agent_run_id, cutoff_at, created_at')


def growth_ready(conn) -> bool:
    """True when migration 027 has run: all eleven tables exist. One ``to_regclass`` query, resolved through
    the search_path, that never raises on a missing table."""
    checks = ' AND '.join(f"to_regclass('{table}') IS NOT NULL" for table in GROWTH_TABLES)
    return bool(conn.execute(f'SELECT {checks}').fetchone()[0])


def try_advisory_lock(conn, key: str) -> bool:
    """Transaction-scoped, non-blocking advisory lock on ``key``; False when another transaction holds it."""
    return bool(conn.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s))', (key,)).fetchone()[0])


_NY = ZoneInfo('America/New_York')


def _ny_date(moment: datetime) -> date:
    return moment.astimezone(_NY).date()


def _open_before(conn, ticker: str, since: date) -> bool:
    """True when the ticker still has shares after the live trades executed before ``since`` (New York date).
    A running quantity with the splits applied the way ``positions.build`` does; it does not match lots to
    units, so it errs towards True (the caller then lets ``retro.end_event`` decide). The real account's trades
    only: retro cards are about what you actually did (spec D4/D18)."""
    pending = split_events(conn, ticker)
    quantity = Decimal(0)
    for side, qty, day in conn.execute(
            "SELECT side, quantity, (executed_at AT TIME ZONE 'America/New_York')::date FROM trades "
            "WHERE ticker = %s AND voided_at IS NULL AND (executed_at AT TIME ZONE 'America/New_York')::date < %s "
            'AND account_id = %s ORDER BY executed_at, id',
            (ticker.upper(), since, REAL_ACCOUNT_ID)).fetchall():
        while pending and pending[0][0] <= day:
            quantity *= pending.pop(0)[1]
        quantity += Decimal(str(qty)) if side == 'buy' else -Decimal(str(qty))
    return quantity > 0


def retro_subjects(conn, *, since: date, until: date) -> list[dict[str, Any]]:
    """Everything ``retro.end_event`` needs from the ledger, one dict per card subject (spec §4.1, D1).
    ``since`` / ``until`` are the END-session window: every subject that could end inside it is returned, so a
    unit signalled or bought long before ``since`` is found while its position is open or closes inside the
    window. Pruning is cheap and errs towards keeping (``end_event`` decides):

    * a ``unit``: a buy unit (some decision proposed ``buy``) with signal date <= ``until``, with its head
      decision, every decision id of the chain (``decision_ids``) and the chain itself oldest first as
      ``decisions`` (``{id, plan_id, decided_at}``, read by the reference plan of a held unit), the live buys
      on any of those decisions, its plans and its review rows (the caller adds bars, books and shadow
      results). Dropped only when it has live buys, none
      of them on/after ``since`` and its ticker was flat before ``since`` (closed before the window), or when
      it has no buys and a review card already recorded its end;
    * a ``trade``: a live buy with no decision executed (New York date) on/before ``until``, dropped under the
      same flat-before-``since`` rule.

    Units come first (by signal date, ticker), then trades by execution time."""
    subjects: list[dict[str, Any]] = []
    flat: dict[str, bool] = {}

    def closed_before(ticker: str) -> bool:
        if ticker not in flat:
            flat[ticker] = not _open_before(conn, ticker, since)
        return flat[ticker]

    for unit in _all(conn, "SELECT DISTINCT ticker, signal_date FROM signal_decisions WHERE proposed_action = 'buy' "
                           'AND signal_date <= %s ORDER BY signal_date, ticker', (until,)):
        ticker, signal_date = unit['ticker'], unit['signal_date']
        chain = _all(conn, 'SELECT id, plan_id, decided_at FROM signal_decisions WHERE ticker = %s '
                           'AND signal_date = %s ORDER BY decided_at, id', (ticker, signal_date))
        decision_ids = [row['id'] for row in chain]
        buys = _all(conn, f"SELECT {_TRADE_COLUMNS} FROM trades WHERE side = 'buy' AND voided_at IS NULL "
                          'AND decision_id = ANY(%s) AND account_id = %s ORDER BY executed_at, id',
                    (decision_ids, REAL_ACCOUNT_ID))
        if buys:
            if all(_ny_date(b['executed_at']) < since for b in buys) and closed_before(ticker):
                continue
        elif conn.execute("SELECT 1 FROM review_cards WHERE subject = 'unit' AND ticker = %s AND signal_date = %s",
                          (ticker, signal_date)).fetchone():
            continue
        subjects.append({'kind': 'unit', 'ticker': ticker, 'signal_date': signal_date, 'buy_trade_id': None,
                         'decision': decision_head(conn, ticker, signal_date, timeline=True),
                         'decision_ids': decision_ids, 'decisions': chain, 'buys': buys,
                         'plans': plans_for_unit(conn, ticker, signal_date),
                         'reviews': chain_reviews(conn, ticker, signal_date)})
    for buy in _all(conn, f"SELECT {_TRADE_COLUMNS} FROM trades WHERE side = 'buy' AND decision_id IS NULL "
                          "AND voided_at IS NULL AND (executed_at AT TIME ZONE 'America/New_York')::date <= %s "
                          'AND account_id = %s ORDER BY executed_at, id', (until, REAL_ACCOUNT_ID)):
        if _ny_date(buy['executed_at']) < since and closed_before(buy['ticker']):
            continue
        subjects.append({'kind': 'trade', 'ticker': buy['ticker'], 'signal_date': None, 'buy_trade_id': buy['id'],
                         'decision': None, 'decision_ids': [], 'decisions': [], 'buys': [buy], 'plans': [],
                         'reviews': []})
    return subjects


# review cards, drafts and verdicts

def insert_review_card(conn, row: dict[str, Any]) -> int | None:
    """The new card's id, or None when ``event_key`` already has one (one card per end event, D4).
    ``facts`` is stored as JSONB, which does not keep key order: readers order by ``retro.FACT_KEYS``."""
    found = conn.execute(
        'INSERT INTO review_cards (event_key, subject, ticker, signal_date, buy_trade_id, end_kind, end_session, '
        'retro_version, facts, detected_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) '
        'ON CONFLICT (event_key) DO NOTHING RETURNING id',
        (row['event_key'], row['subject'], row['ticker'], row.get('signal_date'), row.get('buy_trade_id'),
         row['end_kind'], row['end_session'], row['retro_version'], _plan_json(row['facts']),
         row.get('detected_run_id'))).fetchone()
    return found[0] if found else None


def review_card(conn, card_id: int) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_CARD_COLUMNS} FROM review_cards WHERE id = %s', (card_id,))


def review_card_by_key(conn, event_key: str) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_CARD_COLUMNS} FROM review_cards WHERE event_key = %s', (event_key,))


def review_cards(conn, *, ticker: str | None = None, since: date | None = None) -> list[dict[str, Any]]:
    """Cards newest end first; ``since`` keeps those that ended on or after it."""
    clauses, params = [], []
    if ticker is not None:
        clauses.append('ticker = %s')
        params.append(ticker.upper())
    if since is not None:
        clauses.append('end_session >= %s')
        params.append(since)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    return _all(conn, f'SELECT {_CARD_COLUMNS} FROM review_cards{where} ORDER BY end_session DESC, id DESC', params)


def insert_card_draft(conn, row: dict[str, Any]) -> int:
    output = row.get('output')
    return conn.execute(
        'INSERT INTO review_card_drafts (card_id, attempt, trigger, agent_run_id, status, reason, output) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id',
        (row['card_id'], row['attempt'], row['trigger'], row.get('agent_run_id'), row['status'], row.get('reason'),
         _plan_json(output) if output is not None else None)).fetchone()[0]


def card_drafts(conn, card_id: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_CARD_DRAFT_COLUMNS} FROM review_card_drafts WHERE card_id = %s '
                      'ORDER BY attempt', (card_id,))


def insert_card_verdict(conn, row: dict[str, Any]) -> int:
    return conn.execute(
        'INSERT INTO review_card_verdicts (card_id, request_id, request_hash, draft_id, note) '
        'VALUES (%s, %s, %s, %s, %s) RETURNING id',
        (row['card_id'], row['request_id'], row['request_hash'], row.get('draft_id'),
         row.get('note') or '')).fetchone()[0]


def card_verdict(conn, card_id: int) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_CARD_VERDICT_COLUMNS} FROM review_card_verdicts WHERE card_id = %s', (card_id,))


# my knowledge: items, versions, status records

def insert_knowledge_item(conn, row: dict[str, Any]) -> int:
    return conn.execute(
        'INSERT INTO knowledge_items (origin, source_card_id, source_discussion_id) VALUES (%s, %s, %s) '
        'RETURNING id', (row['origin'], row.get('source_card_id'), row.get('source_discussion_id'))).fetchone()[0]


def knowledge_item(conn, knowledge_id: int) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_KNOWLEDGE_ITEM_COLUMNS} FROM knowledge_items WHERE id = %s', (knowledge_id,))


def insert_knowledge_version(conn, row: dict[str, Any]) -> int:
    return conn.execute(
        'INSERT INTO knowledge_versions (knowledge_id, version, supersedes_id, request_id, request_hash, draft_id, '
        'title, body, scope, counter_examples, concepts, tickers, schema_version) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
        (row['knowledge_id'], row['version'], row.get('supersedes_id'), row['request_id'], row['request_hash'],
         row.get('draft_id'), row['title'], row['body'], row['scope'], row.get('counter_examples') or '',
         list(row.get('concepts') or []), list(row.get('tickers') or []), row['schema_version'])).fetchone()[0]


def insert_knowledge_status(conn, row: dict[str, Any]) -> int:
    return conn.execute(
        'INSERT INTO knowledge_status (knowledge_id, status, reason, request_id, request_hash) '
        'VALUES (%s, %s, %s, %s, %s) RETURNING id',
        (row['knowledge_id'], row['status'], row.get('reason') or '', row['request_id'],
         row['request_hash'])).fetchone()[0]


def knowledge_rows(conn, *, cutoff: datetime | None, ticker: str | None = None, concept: str | None = None,
                   knowledge_id: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Version rows and status rows written at or before ``cutoff`` (None = no limit), the input of
    ``knowledge_items.visible``.

    The filters pick whole items: an item qualifies when its highest version written by the cutoff has the
    ``ticker``, a ``concepts`` entry of that concept id (``concept:<id>@<n>``, matched on the whole id) and/or
    the ``knowledge_id``; all of a qualifying item's versions and status records up to the cutoff come back, so
    the caller's ``visible`` still sees the full history. Nothing written after the cutoff is returned."""
    clauses, params = [], []
    if knowledge_id is not None:
        clauses.append('t.knowledge_id = %s')
        params.append(knowledge_id)
    if ticker is not None:
        clauses.append('%s = ANY(t.tickers)')
        params.append(ticker.upper())
    if concept is not None:
        clauses.append('EXISTS (SELECT 1 FROM unnest(t.concepts) AS c WHERE starts_with(c, %s))')
        params.append(f'concept:{concept}@')
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    versions = _all(
        conn,
        f'WITH seen AS (SELECT {_KNOWLEDGE_VERSION_COLUMNS} FROM knowledge_versions '
        'WHERE (%s::timestamptz IS NULL OR created_at <= %s)), '
        'top AS (SELECT DISTINCT ON (knowledge_id) * FROM seen ORDER BY knowledge_id, version DESC) '
        f'SELECT s.* FROM seen s WHERE s.knowledge_id IN (SELECT t.knowledge_id FROM top t{where}) '
        'ORDER BY s.knowledge_id, s.version', [cutoff, cutoff, *params])
    if not versions:
        return [], []
    statuses = _all(conn, f'SELECT {_KNOWLEDGE_STATUS_COLUMNS} FROM knowledge_status WHERE knowledge_id = ANY(%s) '
                          'AND (%s::timestamptz IS NULL OR created_at <= %s) ORDER BY created_at, id',
                    (sorted({v['knowledge_id'] for v in versions}), cutoff, cutoff))
    return versions, statuses


# lesson drafts and their decisions

def insert_knowledge_draft(conn, row: dict[str, Any]) -> int:
    return conn.execute(
        'INSERT INTO knowledge_drafts (origin, card_draft_id, lesson_index, discussion_turn_id, '
        'target_knowledge_id, target_version, title, body, scope, counter_examples, concepts, tickers, refs) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id',
        (row['origin'], row.get('card_draft_id'), row.get('lesson_index'), row.get('discussion_turn_id'),
         row.get('target_knowledge_id'), row.get('target_version'), row['title'], row['body'], row['scope'],
         row['counter_examples'], list(row.get('concepts') or []), list(row.get('tickers') or []),
         _plan_json(row.get('refs') or []))).fetchone()[0]


def knowledge_drafts(conn, *, open_only: bool, card_draft_id: int | None = None,
                     ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Lesson drafts by id; ``open_only`` keeps those nobody has decided yet."""
    clauses, params = [], []
    if open_only:
        clauses.append('NOT EXISTS (SELECT 1 FROM knowledge_draft_verdicts v WHERE v.draft_id = d.id)')
    if card_draft_id is not None:
        clauses.append('d.card_draft_id = %s')
        params.append(card_draft_id)
    if ids is not None:
        clauses.append('d.id = ANY(%s)')
        params.append(list(ids))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    columns = ', '.join('d.' + c.strip() for c in _KNOWLEDGE_DRAFT_COLUMNS.split(','))
    return _all(conn, f'SELECT {columns} FROM knowledge_drafts d{where} ORDER BY d.id', params)


def insert_draft_verdict(conn, row: dict[str, Any]) -> int:
    return conn.execute(
        'INSERT INTO knowledge_draft_verdicts (draft_id, action, knowledge_version_id, request_id) '
        'VALUES (%s, %s, %s, %s) RETURNING id',
        (row['draft_id'], row['action'], row.get('knowledge_version_id'), row['request_id'])).fetchone()[0]


# citations of knowledge versions by plans and reviews

def insert_knowledge_citations(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            'INSERT INTO knowledge_citations (agent_run_id, plan_id, review_id, knowledge_id, version, field, '
            'call_seq) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            [(r['agent_run_id'], r.get('plan_id'), r.get('review_id'), r['knowledge_id'], r['version'], r['field'],
              r['call_seq']) for r in rows])
    return len(rows)


def citations_of(conn, knowledge_id: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_CITATION_COLUMNS} FROM knowledge_citations WHERE knowledge_id = %s '
                      'ORDER BY id', (knowledge_id,))


def citations_for_review(conn, review_id: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_CITATION_COLUMNS} FROM knowledge_citations WHERE review_id = %s ORDER BY id',
                (review_id,))


def citations_for_plans(conn, plan_ids) -> list[dict[str, Any]]:
    if not plan_ids:
        return []
    return _all(conn, f'SELECT {_CITATION_COLUMNS} FROM knowledge_citations WHERE plan_id = ANY(%s) ORDER BY id',
                (list(plan_ids),))


# discussions and their turns

def insert_discussion(conn, row: dict[str, Any]) -> dict[str, Any]:
    return _one(conn,
                'INSERT INTO discussions (request_id, request_hash, context_kind, ticker, signal_date, plan_id, '
                f'card_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_DISCUSSION_COLUMNS}',
                (row['request_id'], row['request_hash'], row['context_kind'], row['ticker'], row['signal_date'],
                 row.get('plan_id'), row.get('card_id')))


def discussion(conn, discussion_id: int) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_DISCUSSION_COLUMNS} FROM discussions WHERE id = %s', (discussion_id,))


def discussions_for(conn, *, plan_id=None, card_id: int | None = None) -> list[dict[str, Any]]:
    """Discussions opened on a plan or on a review card, oldest first."""
    clauses, params = [], []
    if plan_id is not None:
        clauses.append('plan_id = %s')
        params.append(plan_id)
    if card_id is not None:
        clauses.append('card_id = %s')
        params.append(card_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    return _all(conn, f'SELECT {_DISCUSSION_COLUMNS} FROM discussions{where} ORDER BY id', params)


def insert_turn(conn, row: dict[str, Any]) -> dict[str, Any]:
    attachment = row.get('attachment')
    return _one(conn,
                'INSERT INTO discussion_turns (discussion_id, seq, role, request_id, request_hash, body, '
                'attachment, run_id, reply_to, status, reason, refs, detail, agent_run_id, cutoff_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) '
                f'RETURNING {_TURN_COLUMNS}',
                (row['discussion_id'], row['seq'], row['role'], row.get('request_id'), row.get('request_hash'),
                 row['body'], _plan_json(attachment) if attachment is not None else None, row.get('run_id'),
                 row.get('reply_to'), row.get('status'), row.get('reason'), _plan_json(row.get('refs') or []),
                 _plan_json(row.get('detail') or {}), row.get('agent_run_id'), row.get('cutoff_at')))


def turns(conn, discussion_id: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_TURN_COLUMNS} FROM discussion_turns WHERE discussion_id = %s ORDER BY seq',
                (discussion_id,))


# --- executable plans (v2.9, migration 029): chain_retriggers, holding_status, plan_notes, trades.plan_id ---
# Three append-only tables, deliberately not in WORKBENCH_TABLES; an unmigrated database is detected through
# executable_ready() (the readers answer 503, `daily` refuses to run, spec D36).

_RETRIGGER_COLUMNS = ('id, ticker, unit_signal_date, chain_signal_date, chain_state, rule_ids, baseline_plan_id, '
                      'entry_cap, run_id, timeline_version, created_at')
_HOLDING_STATUS_COLUMNS = ('id, account_id, ticker, session, chain_signal_date, plan_id, review_id, run_id, '
                           'quantity, avg_cost, last_close, stop, target1, stop_mode, t1_reached, reached_1r, '
                           'to_stop_pct, to_t1_pct, r_now, held_sessions, max_hold_sessions, remaining_sessions, '
                           'instruction, exit_reason, reason, detail, timeline_version, created_at')
_PLAN_NOTE_COLUMNS = ('id, request_id, request_hash, ticker, chain_signal_date, plan_id, kind, body, supersedes_id, '
                      'created_at')


def executable_ready(conn) -> bool:
    """True when migration 029 has run: the three new tables, `trades.plan_id` and the two execution columns of
    `trade_plans` all exist (spec §8). A pure existence check; never raises on a missing table."""
    tables = conn.execute("SELECT to_regclass('chain_retriggers'), to_regclass('holding_status'), "
                          "to_regclass('plan_notes'), to_regclass('trades'), to_regclass('trade_plans')").fetchone()
    if any(item is None for item in tables):
        return False
    wanted = {('trades', 'plan_id'), ('trade_plans', 'entry_type'), ('trade_plans', 'max_hold_sessions')}
    found = conn.execute(
        'SELECT attrelid::regclass::text, attname FROM pg_attribute '
        "WHERE attrelid IN (to_regclass('trades'), to_regclass('trade_plans')) "
        "AND attname IN ('plan_id', 'entry_type', 'max_hold_sessions') AND NOT attisdropped").fetchall()
    return wanted <= {(table.rsplit('.', 1)[-1], name) for table, name in found}


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
    inserted = _one(conn,
                    'INSERT INTO holding_status (account_id, ticker, session, chain_signal_date, plan_id, '
                    'review_id, run_id, quantity, avg_cost, last_close, stop, target1, stop_mode, t1_reached, '
                    'reached_1r, to_stop_pct, to_t1_pct, r_now, held_sessions, max_hold_sessions, '
                    'remaining_sessions, instruction, exit_reason, reason, detail, timeline_version) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '
                    '%s, %s, %s, %s::jsonb, %s) ON CONFLICT (account_id, ticker, session) DO NOTHING '
                    f'RETURNING {_HOLDING_STATUS_COLUMNS}',
                    (account_id, ticker, row['session'], row.get('chain_signal_date'), row.get('plan_id'),
                     row.get('review_id'), row['run_id'], row['quantity'], row['avg_cost'], row.get('last_close'),
                     row.get('stop'), row.get('target1'), row.get('stop_mode'), row.get('t1_reached'),
                     row.get('reached_1r'), row.get('to_stop_pct'), row.get('to_t1_pct'), row.get('r_now'),
                     row['held_sessions'], row.get('max_hold_sessions'), row.get('remaining_sessions'),
                     row['instruction'], row.get('exit_reason'), row['reason'], _plan_json(row.get('detail') or {}),
                     row['timeline_version']))
    if inserted is not None:
        return {**inserted, 'replayed': False}
    existing = _one(conn, f'SELECT {_HOLDING_STATUS_COLUMNS} FROM holding_status '
                          'WHERE account_id = %s AND ticker = %s AND session = %s',
                    (account_id, ticker, row['session']))
    return {**existing, 'replayed': True}


def holding_status_for(conn, *, session: date | None = None, ticker: str | None = None,
                       chain_signal_date: date | None = None,
                       account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Holding-status rows matching every given filter, oldest session first (then ticker). One account's by
    default (`account_id=None` reads every account's)."""
    clauses, params = [], []
    if account_id is not None:
        clauses.append('account_id = %s')
        params.append(account_id)
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
    return _all(conn, f'SELECT {_HOLDING_STATUS_COLUMNS} FROM holding_status{where} ORDER BY session, ticker, id',
                params)


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
            from investment_assistant.workbench.ledger import Conflict      # ledger imports this module
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


def retro_subject_keys(conn, *, until: date) -> dict[str, Any]:
    """The subjects a `retro-2` card may be about (v2.9 spec D29), as keys; the caller reads the rest.

    ``units``: ``(ticker, signal_date)`` of every unit or chain that has a live buy executed (New York date) on or
    before ``until`` — linked through ``trades.plan_id`` (a fill linked to the baseline of a unit merged into a chain
    counts for the chain) or, for old data, through a decision of the unit — or that carries a live plan note (a note
    on a chain, or on a rule / own plan, which counts for the plan's unit). ``trades``: the live buys with neither a
    plan nor a decision, oldest first (the plan-less trades)."""
    day = "(t.executed_at AT TIME ZONE 'America/New_York')::date"
    linked = _all(conn,
                  'SELECT DISTINCT t.ticker, CASE '
                  'WHEN EXISTS (SELECT 1 FROM agent_plan_versions v WHERE v.ticker = p.ticker '
                  'AND v.signal_date = p.signal_date) THEN p.signal_date '
                  'ELSE COALESCE((SELECT r.chain_signal_date FROM chain_retriggers r WHERE r.ticker = p.ticker '
                  'AND r.unit_signal_date = p.signal_date), p.signal_date) END AS signal_date '
                  'FROM trades t JOIN trade_plans p ON p.id = t.plan_id '
                  f"WHERE t.side = 'buy' AND t.voided_at IS NULL AND t.account_id = %s AND {day} <= %s",
                  (REAL_ACCOUNT_ID, until))
    legacy = _all(conn,
                  'SELECT DISTINCT d.ticker, d.signal_date FROM signal_decisions d JOIN trades t '
                  "ON t.decision_id IN (SELECT x.id FROM signal_decisions x WHERE x.ticker = d.ticker "
                  "AND x.signal_date = d.signal_date) WHERE t.side = 'buy' AND t.voided_at IS NULL "
                  f"AND t.account_id = %s AND d.signal_date <= %s AND {day} <= %s",
                  (REAL_ACCOUNT_ID, until, until))
    noted = _all(conn,
                 'SELECT DISTINCT n.ticker, COALESCE(n.chain_signal_date, p.signal_date) AS signal_date '
                 'FROM plan_notes n LEFT JOIN trade_plans p ON p.id = n.plan_id '
                 "WHERE n.kind = 'note' AND NOT EXISTS (SELECT 1 FROM plan_notes s WHERE s.supersedes_id = n.id "
                 "AND s.kind = 'retract') AND COALESCE(n.chain_signal_date, p.signal_date) <= %s", (until,))
    plan_less = _all(conn, f"SELECT {_TRADE_COLUMNS} FROM trades t WHERE t.side = 'buy' AND t.voided_at IS NULL "
                           f'AND t.decision_id IS NULL AND t.plan_id IS NULL AND t.account_id = %s AND {day} <= %s '
                           'ORDER BY t.executed_at, t.id', (REAL_ACCOUNT_ID, until))
    keys = sorted({(r['ticker'], r['signal_date']) for r in (*linked, *legacy, *noted)},
                  key=lambda k: (k[1], k[0]))
    return {'units': keys, 'trades': plan_less}


def unit_decision_chain(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """The old decisions of one unit, oldest first, as ``{id, plan_id, decided_at}`` (a retro-2 card needs the plan of
    the decision a fill was attached to, not only the head decision)."""
    return _all(conn, 'SELECT id, plan_id, decided_at FROM signal_decisions WHERE ticker = %s AND signal_date = %s '
                      'ORDER BY decided_at, id', (ticker.upper(), signal_date))


# --- v2.11 accounts and paper trading (migration 030, spec §7) ---
# Six new tables, deliberately not in WORKBENCH_TABLES: an unmigrated database degrades through
# accounts_ready() — the account reads answer 503 and `daily` refuses to run (spec D22). REAL_ACCOUNT_ID
# (top of this module) scopes every pre-030 reader above, so the existing call sites are unchanged (D4).

_ACCOUNT_COLUMNS = ('id, kind, strategy, name, opening_cash, opened_on, params, status, closed_at, close_reason, '
                    'settled_through, created_at')


def accounts_ready(conn) -> bool:
    """True when migration 030 has run: the six new tables and `trades.account_id` all exist (spec §7).
    A pure existence check; never raises on a missing table."""
    tables = conn.execute(
        "SELECT to_regclass('accounts'), to_regclass('account_param_changes'), to_regclass('account_equity_days'), "
        "to_regclass('paper_orders'), to_regclass('paper_decisions'), to_regclass('paper_chain_done')").fetchone()
    if any(item is None for item in tables):
        return False
    has_account = conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = 'trades'::regclass "
                               "AND attname = 'account_id' AND NOT attisdropped").fetchone()
    return bool(has_account)


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
    """Active paper accounts' traded tickers plus their pending buys' (D29): the `collect` scope keeps fetching
    bars for tickers that may have left the pool while a paper position or order still needs them. Errs towards
    keeping — every unvoided trade of an active paper account counts, closed positions included."""
    rows = conn.execute(
        'SELECT DISTINCT t.ticker FROM trades t JOIN accounts a ON a.id = t.account_id '
        "WHERE a.kind = 'paper' AND a.status = 'active' AND t.voided_at IS NULL "
        'UNION SELECT DISTINCT o.ticker FROM paper_orders o JOIN accounts a ON a.id = o.account_id '
        "WHERE a.kind = 'paper' AND a.status = 'active' AND o.status = 'pending'").fetchall()
    return {row[0] for row in rows}


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
                to_session: date | None = None) -> list[dict[str, Any]]:
    """The account's equity per session — the latest `computed_at` row of each (D15) — oldest session first;
    `from_session` / `to_session` bound the range (inclusive)."""
    clauses, params = ['account_id = %s'], [account_id]
    if from_session is not None:
        clauses.append('session >= %s')
        params.append(from_session)
    if to_session is not None:
        clauses.append('session <= %s')
        params.append(to_session)
    return _all(conn, f'SELECT DISTINCT ON (session) {_EQUITY_DAY_COLUMNS} FROM account_equity_days '
                      f"WHERE {' AND '.join(clauses)} ORDER BY session, computed_at DESC, id DESC", params)


def latest_equity_day(conn, account_id, *, on_or_before: date) -> dict[str, Any] | None:
    """The newest equity row of the session at or before the date (D6: that day's ledger equity)."""
    return _one(conn, f'SELECT {_EQUITY_DAY_COLUMNS} FROM account_equity_days WHERE account_id = %s '
                      'AND session <= %s ORDER BY session DESC, computed_at DESC, id DESC LIMIT 1',
                (account_id, on_or_before))


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
    """Move a pending order to its outcome once (§7.5: the status columns are the one updatable pair, `updated_at`
    records it); None when the order is no longer pending, so a replayed settlement cannot overwrite the first
    outcome."""
    return _one(conn,
                'UPDATE paper_orders SET status = %s, status_reason = %s, settled_on = %s, trade_id = %s, '
                "updated_at = clock_timestamp() WHERE id = %s AND status = 'pending' "
                f'RETURNING {_ORDER_COLUMNS}',
                (status, status_reason, settled_on, trade_id, order_id))


def tried_plan_ids(conn, account_id) -> set[uuid.UUID]:
    """The plan versions this account has ever placed a buy order on, any status (D24's per-version filter:
    an order that expired only ends its version, not the chain)."""
    rows = conn.execute('SELECT DISTINCT plan_id FROM paper_orders WHERE account_id = %s '
                        "AND action = 'buy' AND plan_id IS NOT NULL", (account_id,)).fetchall()
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
