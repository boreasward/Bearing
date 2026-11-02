"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
import json
from datetime import date, datetime
from collections.abc import Sequence
from typing import Any
from psycopg.rows import dict_row
from investment_assistant.core.idempotency import Conflict
from investment_assistant.core.dbutil import _one, _all, _canonical, _plan_json

from investment_assistant.core.versions import REGIME_VERSION
from investment_assistant.data.repo import _REGIME_COLUMNS, _AGENT_RUN_COLUMNS
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import repo as execution_repo

_RESEARCH_TRANSITIONS = {
    'queued': frozenset({'running', 'closed'}),
    'running': frozenset({'queued', 'waiting_data', 'concluded', 'closed'}),
    'waiting_data': frozenset({'running', 'closed'}),
    'concluded': frozenset({'closed'}),
    'closed': frozenset(),
}


def insert_research_request(conn, row) -> dict[str, Any]:
    """Create a queued dossier; identical idempotency keys replay even after status changes."""
    if row.get('status', 'queued') != 'queued':
        raise ValueError('research_initial_status')
    values = (row['ticker'].upper(), row.get('question'), row.get('horizon_days'), row.get('source', 'manual'))
    saved = _one(conn, 'INSERT INTO research_requests '
        '(id, ticker, question, horizon_days, source, status, request_id, created_at) '
        "VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s) ON CONFLICT (request_id) DO NOTHING RETURNING *",
        (row['id'], *values, row['request_id'], row['created_at']))
    if saved is not None:
        return saved
    saved = _one(conn, 'SELECT * FROM research_requests WHERE request_id = %s', (row['request_id'],))
    assert saved is not None
    if tuple(saved[k] for k in ('ticker', 'question', 'horizon_days', 'source')) != values:
        raise Conflict('research_request_conflict')
    return saved


def update_research_status(conn, id, *, from_status, to_status, **fields) -> bool:
    """D51: legal repeated transitions use compare-and-set, never an unguarded overwrite."""
    if to_status not in _RESEARCH_TRANSITIONS.get(from_status, ()):
        raise ValueError('research_status_transition')
    if set(fields) - {'attempts', 'updated_at', 'closed_reason'}:
        raise ValueError('research_status_fields')
    if 'attempts' in fields and (type(fields['attempts']) is not int or fields['attempts'] < 0):
        raise ValueError('research_attempts')
    assignments = ['status = %s', 'updated_at = COALESCE(%s, clock_timestamp())']
    params = [to_status, fields.get('updated_at')]
    for name in ('attempts', 'closed_reason'):
        if name in fields:
            assignments.append(f'{name} = %s')
            params.append(fields[name])
    return conn.execute('UPDATE research_requests SET ' + ', '.join(assignments) +
        ' WHERE status = %s AND id = %s', (*params, from_status, id)).rowcount == 1


def research_request(conn, id) -> dict[str, Any] | None:
    return _one(conn, 'SELECT * FROM research_requests WHERE id = %s', (id,))


def list_research_requests(conn, *, status=None, limit=50):
    where = ' WHERE status = %s' if status is not None else ''
    return _all(conn, 'SELECT * FROM research_requests' + where + ' ORDER BY created_at DESC, id DESC LIMIT %s',
                ([status] if status is not None else []) + [limit])


def queued_research_requests(conn):
    """Waiting-data dossiers are eligible for the next batch, FIFO with queued ones."""
    return _all(conn, "SELECT * FROM research_requests WHERE status IN ('queued', 'waiting_data') "
                'ORDER BY created_at, id', ())


def stale_research_requests(conn, *, today: date, days: int):
    """D28: open dossiers (queued / waiting_data / concluded; a running attempt is never closed under it) whose
    newest version, or their creation when they have none, lies more than ``days`` New York calendar days before
    ``today``."""
    return _all(conn, "SELECT r.* FROM research_requests r "
                "WHERE r.status IN ('queued', 'waiting_data', 'concluded') "
                "AND %s::date - (COALESCE((SELECT max(v.created_at) FROM research_versions v WHERE v.request_id = r.id), "
                "r.created_at) AT TIME ZONE 'America/New_York')::date > %s ORDER BY r.created_at, r.id",
                (today, days))


def stuck_running_research_requests(conn, *, before):
    """B4: ``running`` requests whose last move (the switch to ``running``) is older than ``before`` -- a dossier whose
    process died; a live attempt never outlasts its deadline."""
    return _all(conn, "SELECT * FROM research_requests WHERE status = 'running' AND updated_at < %s "
                'ORDER BY created_at, id', (before,))


def insert_research_version(conn, row) -> dict[str, Any]:
    """Append a conclusion/error version; retain SQL NULL rather than JSON null for absent outputs."""
    saved = _one(conn, 'INSERT INTO research_versions '
        '(request_id, version_no, agent_run_id, cutoff_at, status, action, plan_id, summary, output, '
        'review_when, tokens, elapsed_ms, created_at) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, '
        'COALESCE(%s, clock_timestamp())) RETURNING *',
        (row['request_id'], row['version_no'], row.get('agent_run_id'), row['cutoff_at'], row['status'],
         row.get('action'), row.get('plan_id'), row.get('summary'),
         _plan_json(row['output']) if row.get('output') is not None else None,
         _plan_json(row['review_when']) if row.get('review_when') is not None else None,
         row.get('tokens'), row.get('elapsed_ms'), row.get('created_at')))
    assert saved is not None
    return saved


def research_versions(conn, request_id):
    return _all(conn, 'SELECT * FROM research_versions WHERE request_id = %s ORDER BY version_no, id', (request_id,))


def latest_dossier_for(conn, ticker, *, cutoff_at) -> dict[str, Any] | None:
    """Newest actual conclusion known by cutoff, not a backfilled/future version or error-only attempt."""
    return _one(conn, 'SELECT v.*, r.ticker, r.question, r.horizon_days FROM research_versions v '
        'JOIN research_requests r ON r.id = v.request_id '
        "WHERE r.ticker = %s AND v.status IN ('concluded', 'waiting_data') "
        'AND r.created_at <= %s AND v.created_at <= %s AND v.cutoff_at <= %s '
        'ORDER BY v.cutoff_at DESC, v.created_at DESC, v.version_no DESC, v.id DESC LIMIT 1',
        (ticker.upper(), cutoff_at, cutoff_at, cutoff_at))

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


def _regime_fingerprint(snap: dict[str, Any]) -> str:
    compared = {k: snap[k] for k in ('label', 'breadth', 'sectors', 'macro')}
    return json.dumps(compared, sort_keys=True, separators=(',', ':'), default=str)


def store_regime(conn, snap: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Insert a snapshot; same content returns the stored row, different content is a conflict."""
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


def latest_regime(conn, *, on_or_before: date, cutoff_at: datetime) -> dict[str, Any] | None:
    """The newest regime-1 snapshot at or before the date, visible by the cutoff."""
    return _one(conn, f'SELECT {_REGIME_COLUMNS} FROM market_regimes '
                      'WHERE session <= %s AND calc_version = %s AND created_at <= %s '
                      'ORDER BY session DESC, created_at DESC LIMIT 1',
                (on_or_before, REGIME_VERSION, cutoff_at))


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
    assert record is not None
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO screen_results (screen_run_id, ticker, screen_group, trigger_rules, filters, '
                        'rs, atr_pct, dollar_volume_20, rank, selected) '
                        'VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)',
                        [(record['id'], r['ticker'], r['group'], r.get('trigger_rules') or [],
                          _plan_json(r['filters']), r.get('rs'), r.get('atr_pct'),
                          r.get('dollar_volume_20'), r.get('rank'), r['selected']) for r in results])
    return record


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
    tickers |= execution_repo.open_position_tickers(conn)
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


_ATTEMPT_RESET_COLUMNS = 'id, ticker, signal_date, mode, request_id, origin, created_at'


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
    assert existing is not None
    return {**existing, 'replayed': True}


def attempt_resets_for(conn, ticker: str, signal_date: date, mode: str) -> list[dict[str, Any]]:
    """A decision unit's reset records for one mode, newest first."""
    return _all(conn, f'SELECT {_ATTEMPT_RESET_COLUMNS} FROM agent_attempt_resets '
                      'WHERE ticker = %s AND signal_date = %s AND mode = %s ORDER BY created_at DESC, id DESC',
                (ticker, signal_date, mode))


def candidate_refs_by_ids(conn, ids) -> list[dict[str, Any]]:
    return _all(conn, 'SELECT id, ticker, signal_date, rule_id FROM signal_candidates WHERE id = ANY(%s::uuid[])',
                (list(map(str, ids)),))


def agent_runs_by_ids(conn, ids) -> list[dict[str, Any]]:
    """Run rows without the output / counter payloads (the detail page lists them, the reasoning view opens one)."""
    return _all(conn, 'SELECT id, ticker, signal_date, mode, status, reason, attempt, model, plan_id, input_tokens, '
                      'output_tokens, created_at FROM agent_runs WHERE id = ANY(%s::uuid[]) '
                      'ORDER BY ticker, signal_date, mode, created_at', (list(map(str, ids)),))


def insert_agent_run(conn, run: dict[str, Any], calls: list[dict[str, Any]], *,
                      data_requests: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """The run row, all its tool calls and (D41/R14) the requests its tools queued, in the caller's
    transaction (§3.3 order: plan, run, calls; the plan is inserted by the service before this call).

    ``data_requests`` items are the Recorder's ``{'family', 'ticker'}`` entries (optionally with
    ``run_id`` / ``requested_at``); the run's own id and cutoff fill the rest. Empty keeps the old
    behavior exactly: no new-table access at all."""
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
    assert record is not None
    for c in calls:
        conn.execute('INSERT INTO agent_tool_calls (run_id, seq, phase, tool, args, result, result_sha256, '
                     'called_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)',
                     (run['id'], c['seq'], c['phase'], c['tool'], _plan_json(c['args']),
                      _plan_json(c['result']), c['result_sha256'], c['called_at']))
    if data_requests:
        data_repo.insert_data_requests(conn, [
            {'run_id': row.get('run_id'), 'agent_run_id': run['id'], 'family': row['family'],
             'ticker': row['ticker'], 'requested_at': row.get('requested_at') or run['cutoff_at']}
            for row in data_requests])
    return record


def agent_runs_for(conn, ticker: str, signal_date: date, mode: str = 'propose') -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_AGENT_RUN_COLUMNS} FROM agent_runs WHERE ticker = %s AND signal_date = %s '
                      'AND mode = %s ORDER BY created_at, id', (ticker.upper(), signal_date, mode))


def latest_agent_run(conn, ticker: str, signal_date: date, mode: str = 'propose') -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_AGENT_RUN_COLUMNS} FROM agent_runs WHERE ticker = %s AND signal_date = %s '
                      'AND mode = %s ORDER BY created_at DESC, id DESC LIMIT 1',
                (ticker.upper(), signal_date, mode))


def unit_runs_with_research(conn, version: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Plan-producing runs (propose / revise) of one agent run version whose cutoff falls in ``[start, end)`` and that
    kept an output: ``id, ticker, signal_date, mode, status`` and the output's ``research`` segment (None when absent)."""
    return _all(conn, 'SELECT id, ticker, signal_date, mode, status, output -> %s AS research FROM agent_runs '
                      "WHERE agent_version = %s AND mode IN ('propose', 'revise') AND output IS NOT NULL "
                      'AND cutoff_at >= %s AND cutoff_at < %s ORDER BY created_at, id',
                ('research', version, start, end))


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
