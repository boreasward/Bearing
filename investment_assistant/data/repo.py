"""Data facts, acquisition records and cross-layer read-only facts; caller owns transactions."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo
from psycopg.rows import dict_row
from investment_assistant.core import knowledge, schema
from investment_assistant.core.dbutil import _one, _all, _plan_json
from investment_assistant.core.versions import REGIME_VERSION


def plan_knowledge_refs(conn, plan_ids) -> dict[str, list[dict]]:
    """Read-only citation facts for plan readers; grouping and reference formatting live in core."""
    ids = [str(p) for p in plan_ids if p is not None]
    if not ids or not schema.growth_ready(conn):
        return {}
    rows = conn.execute('SELECT plan_id, knowledge_id, version, field FROM knowledge_citations '
                        'WHERE plan_id = ANY(%s) ORDER BY id', (ids,)).fetchall()
    if not rows:
        return {}
    citations = [dict(zip(('plan_id', 'knowledge_id', 'version', 'field'), row)) for row in rows]
    versions = [dict(zip(('knowledge_id', 'version', 'title'), row)) for row in conn.execute(
        'SELECT knowledge_id, version, title FROM knowledge_versions').fetchall()]
    return knowledge.refs_for_plans(citations, versions)

# Acquisition metadata is not an economic revision. Keep the original pointer/hash
# together on consecutive repeats, even when another fact changes the response.
_PROVENANCE = {'id', 'source_url', 'source_hash', 'source_locator', 'snapshot_path',
               'collected_at', 'raw_snapshots', 'snapshots', 'first_seen_at', 'last_seen_at',
               'revision', 'security_id', 'dataset', 'natural_key', 'disclosed_at'}


def _economic(value):
    if isinstance(value, dict):
        return {k: _economic(v) for k, v in value.items() if k not in _PROVENANCE}
    if isinstance(value, list):
        return [_economic(v) for v in value]
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def store_rows(conn, sid, dataset, rows, *, now=None):
    """Return one persisted record per input (including deduplicated inputs).

    Each input is {natural_key, payload, disclosed_at?, source_url?, source_hash?}.
    Dataset-level serialization includes absent-row creation. Dataset acquisition
    ordering is additionally protected by the service lock held before remote I/O.
    """
    now = now or datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('us-research:' + sid + ':' + dataset,))
        ids = []
        for row in rows:
            payload = dict(row['payload'])
            payload.pop('id', None)  # Adapter hashes are never database references.
            # Human guidance edits include source-only corrections. Acquisition-only
            # metadata is not part of its authored content, but URL/locator are.
            content = ({k: v for k, v in payload.items() if k not in {'collected_at', 'first_seen_at', 'last_seen_at', 'revision', 'snapshot_path'}}
                       if dataset == 'guidance' else _economic(payload))
            fingerprint = hashlib.sha256(_json({'payload': content,
                'disclosed_at': str(row.get('disclosed_at'))}).encode()).hexdigest()
            cur.execute('''SELECT id,revision,content_hash FROM us_research_observations
                WHERE security_id=%s AND dataset=%s AND natural_key=%s ORDER BY id DESC LIMIT 1''',
                (sid, dataset, row['natural_key']))
            old = cur.fetchone()
            if old and old[2] == fingerprint:
                cur.execute('UPDATE us_research_observations SET last_seen_at=%s WHERE id=%s', (now, old[0]))
                ids.append(old[0])
                continue
            revision = hashlib.sha256((fingerprint + (old[1] if old else '')).encode()).hexdigest()
            cur.execute('''INSERT INTO us_research_observations
                (security_id,dataset,natural_key,revision,content_hash,payload,source_url,source_hash,
                 disclosed_at,first_seen_at,last_seen_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) RETURNING id''',
                (sid, dataset, row['natural_key'], revision, fingerprint, _json(payload), row.get('source_url'),
                 row.get('source_hash'), row.get('disclosed_at'), now, now))
            ids.append(cur.fetchone()[0])
        if not ids:
            return []
        cur.execute('''SELECT id,payload,security_id,dataset,natural_key,revision,source_url,source_hash,
            disclosed_at,first_seen_at,last_seen_at FROM us_research_observations WHERE id=ANY(%s)''', (ids,))
        records = {r[0]: _record(r) for r in cur.fetchall()}
        return [records[id_] for id_ in ids]


def _record(r):
    return {**r[1], 'id': r[0], 'security_id': r[2], 'dataset': r[3], 'natural_key': r[4], 'revision': r[5],
            'source_url': r[6] or r[1].get('source_url'), 'source_hash': r[7] or r[1].get('source_hash'),
            'disclosed_at': r[8], 'first_seen_at': r[9], 'last_seen_at': r[10]}


def _read(conn, sid, dataset, *, as_of=None, history=False):
    params = [sid, dataset]
    cutoff = ''
    if as_of is not None:
        cutoff = 'AND first_seen_at<=%s AND (disclosed_at IS NULL OR disclosed_at<=%s)'
        params.extend([as_of, as_of])
    distinct = '' if history else 'DISTINCT ON (natural_key)'
    order = 'id' if history else 'natural_key,id DESC'
    with conn.cursor() as cur:
        cur.execute(f'''SELECT {distinct} id,payload,security_id,dataset,natural_key,revision,source_url,source_hash,
            disclosed_at,first_seen_at,last_seen_at FROM us_research_observations
            WHERE security_id=%s AND dataset=%s {cutoff} ORDER BY {order}''', params)
        return [_record(r) for r in cur.fetchall()]


def read_rows(conn, sid, dataset, *, as_of=None):
    """Current eligible revision per natural key, local observation/disclosure cutoff."""
    return _read(conn, sid, dataset, as_of=as_of)


def read_history(conn, sid, dataset, *, as_of=None):
    """All eligible append-only revisions in insertion order (including guidance)."""
    return _read(conn, sid, dataset, as_of=as_of, history=True)


def collection_state(conn, sid, dataset, *, status, count=0, error=None, details=None):
    now = datetime.now(UTC)
    succeeded = now if status in {'ready', 'partial', 'empty'} else None
    details = _json(details or {})
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO us_research_collection_state
            (security_id,dataset,status,attempted_at,succeeded_at,row_count,error,details,last_success_details)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
            ON CONFLICT (security_id,dataset) DO UPDATE SET status=excluded.status,
            attempted_at=excluded.attempted_at,error=excluded.error,details=excluded.details,
            succeeded_at=COALESCE(excluded.succeeded_at,us_research_collection_state.succeeded_at),
            row_count=CASE WHEN excluded.succeeded_at IS NULL THEN us_research_collection_state.row_count ELSE excluded.row_count END,
            last_success_details=CASE WHEN excluded.succeeded_at IS NULL THEN us_research_collection_state.last_success_details ELSE excluded.last_success_details END''',
            (sid, dataset, status, now, succeeded, count, error, details, details if succeeded else '{}'))


def states(conn, sid):
    keys = ['dataset', 'status', 'attempted_at', 'succeeded_at', 'row_count', 'error', 'details', 'last_success_details']
    with conn.cursor() as cur:
        cur.execute('''SELECT dataset,status,attempted_at,succeeded_at,row_count,error,details,last_success_details
            FROM us_research_collection_state WHERE security_id=%s''', (sid,))
        return {r[0]: dict(zip(keys, r)) for r in cur.fetchall()}


BAR_VALUES = ('open', 'high', 'low', 'close', 'adj_close', 'volume', 'split_factor')
_FLOAT_FIELDS = ('open', 'high', 'low', 'close', 'adj_close', 'split_factor')

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


def store_bars(conn, ticker: str, rows: list[dict[str, Any]], *, now: datetime,
               source: str = 'yfinance') -> dict[str, int]:
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
                f"INSERT INTO price_bars (ticker, price_date, {', '.join(BAR_VALUES)}, revision, fetched_at, source) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(BAR_VALUES))}, %s, %s, %s)",
                (ticker, row['price_date'], *(row[k] for k in BAR_VALUES),
                 (latest['revision'] + 1) if latest else 1, now, source))
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


def split_events(conn, ticker: str, *, cutoff_at: datetime | None = None) -> list[tuple[date, Decimal]]:
    clause = ' AND fetched_at <= %s AND price_date <= %s' if cutoff_at is not None else ''
    params = [ticker.upper()]
    if cutoff_at is not None:
        params += [cutoff_at, cutoff_at.astimezone(ZoneInfo('America/New_York')).date()]
    rows = conn.execute(
        'SELECT price_date, split_factor FROM (SELECT DISTINCT ON (price_date) price_date, split_factor '
        f'  FROM price_bars WHERE ticker = %s{clause} ORDER BY price_date, revision DESC) latest '
        'WHERE split_factor <> 1 ORDER BY price_date', params).fetchall()
    return [(d, Decimal(f)) for d, f in rows]


_EARNINGS_COLUMNS = 'id, ticker, earnings_date, status, source, fetched_at, created_at'


def store_earnings(conn, ticker: str, row: dict[str, Any], *, now: datetime,
                   source: str = 'yfinance') -> dict[str, Any]:
    """Append only when the answer changed; a same-day re-fetch writes nothing."""
    latest = latest_earnings(conn, ticker, cutoff_at=now)
    if latest and latest['status'] == row['status'] and latest['earnings_date'] == row.get('earnings_date'):
        return {'status': 'unchanged', 'id': latest['id']}
    with conn.cursor() as cur:
        cur.execute('INSERT INTO earnings_dates (ticker, earnings_date, status, fetched_at, source) '
                    'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                    (ticker.upper(), row.get('earnings_date'), row['status'], now, source))
        return {'status': 'inserted', 'id': cur.fetchone()[0]}


def latest_earnings(conn, ticker: str, *, cutoff_at: datetime) -> dict[str, Any] | None:
    """The newest earnings answer known by ``cutoff_at``."""
    return _one(conn, f'SELECT {_EARNINGS_COLUMNS} FROM earnings_dates WHERE ticker = %s AND fetched_at <= %s '
                      'ORDER BY fetched_at DESC, created_at DESC LIMIT 1', (ticker.upper(), cutoff_at))


def earnings_row_by_id(conn, earnings_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_EARNINGS_COLUMNS} FROM earnings_dates WHERE id = %s', (earnings_id,))


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


def regime_for(conn, session: date, *, cutoff_at: datetime) -> dict[str, Any] | None:
    """The regime-1 snapshot of that session, as visible by the cutoff."""
    return _one(conn, f'SELECT {_REGIME_COLUMNS} FROM market_regimes '
                      'WHERE session = %s AND calc_version = %s AND created_at <= %s',
                (session, REGIME_VERSION, cutoff_at))


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


def fetch_runs_by_ids(conn, ids) -> list[dict[str, Any]]:
    """The `data_fetch_runs` rows a stage row refers to, in the order of `ids`."""
    rows = _all(conn, f'SELECT {_FETCH_RUN_COLUMNS} FROM data_fetch_runs WHERE id = ANY(%s)', (list(ids),))
    order = {int(i): n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda row: order.get(int(row['id']), len(order)))


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


_AGENT_RUN_COLUMNS = ('id, ticker, signal_date, pool, attempt, mode, agent_version, provider, model, cutoff_at, '
                      'status, reason, detail, output, counter, plan_id, input_tokens, output_tokens, '
                      'started_at, finished_at, created_at')


def agent_run_by_id(conn, run_id) -> dict[str, Any] | None:
    return _one(conn, f'SELECT {_AGENT_RUN_COLUMNS} FROM agent_runs WHERE id = %s', (run_id,))


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


_KNOWLEDGE_VERSION_COLUMNS = ('id, knowledge_id, version, supersedes_id, request_id, request_hash, draft_id, title, '
                              'body, scope, counter_examples, concepts, tickers, schema_version, created_at')
_KNOWLEDGE_STATUS_COLUMNS = 'id, knowledge_id, status, reason, request_id, request_hash, created_at'


def knowledge_rows(conn, *, cutoff: datetime | None, ticker: str | None = None, concept: str | None = None,
                   knowledge_id: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Version rows and status rows written at or before ``cutoff`` (None = no limit), the input of
    ``core.knowledge.visible``.

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


def insert_data_requests(conn, rows) -> list[int]:
    """Persist buffered tool requests after their agent run, in the caller's transaction."""
    ids = []
    for row in rows:
        if row.get('status', 'queued') != 'queued':
            raise ValueError('data_request_initial_status')
        saved = _one(conn, 'INSERT INTO data_requests '
            '(run_id, agent_run_id, family, ticker, requested_at) VALUES (%s, %s, %s, %s, %s) RETURNING id',
            (row.get('run_id'), row.get('agent_run_id'), row['family'], row['ticker'].upper(),
             row['requested_at']))
        assert saved is not None
        ids.append(saved['id'])
    return ids


def settle_data_request(conn, id, *, status, fetch_run_id, rows, reason, rerun_run_id) -> bool:
    """Only queued→terminal once. Use attach_data_request_rerun after a later rerun."""
    if status not in {'fetched', 'failed', 'deferred'}:
        raise ValueError('data_request_terminal_status')
    if rows is not None and (type(rows) is not int or rows < 0):
        raise ValueError('data_request_rows')
    if rerun_run_id is not None and (status != 'fetched' or not rows):
        raise ValueError('data_request_rerun_without_rows')
    return conn.execute('UPDATE data_requests SET status = %s, fetch_run_id = %s, rows = %s, '
        'reason = %s, rerun_run_id = %s, updated_at = clock_timestamp() '
        "WHERE status = 'queued' AND id = %s",
        (status, fetch_run_id, rows, reason, rerun_run_id, id)).rowcount == 1


def attach_data_request_rerun(conn, id, rerun_run_id) -> bool:
    """Fill only the rerun pointer; settlement status/count/reason/time stay frozen (D40)."""
    if rerun_run_id is None:
        raise ValueError('data_request_rerun_required')
    return conn.execute('UPDATE data_requests SET rerun_run_id = %s '
        "WHERE status = 'fetched' AND id = %s AND rows > 0 AND rerun_run_id IS NULL",
        (rerun_run_id, id)).rowcount == 1


def queued_data_requests(conn, run_id) -> list[dict]:
    return _all(conn, "SELECT * FROM data_requests WHERE status = 'queued' AND run_id IS NOT DISTINCT FROM %s "
                'ORDER BY requested_at, id', (run_id,))


def insert_sector_classification(conn, row) -> int:
    """Append an answer; the adapter decides whether it differs from the prior answer."""
    saved = _one(conn, 'INSERT INTO sector_classifications '
        '(ticker, sic, sic_description, "group", source, fetched_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
        (row['ticker'].upper(), row.get('sic'), row.get('sic_description'), row.get('group'),
         row['source'], row['fetched_at']))
    assert saved is not None
    return saved['id']


def latest_sectors(conn, tickers, *, cutoff_at) -> dict[str, dict]:
    return {row['ticker']: row for row in _all(conn,
        'SELECT DISTINCT ON (ticker) * FROM sector_classifications '
        'WHERE ticker = ANY(%s) AND fetched_at <= %s ORDER BY ticker, fetched_at DESC, id DESC',
        ([ticker.upper() for ticker in tickers], cutoff_at))}


def missing_bar_sessions(conn, ticker, sessions: list[date]) -> list[date]:
    """D46: any existing revision blocks backup filling; preserve requested session order."""
    found = {row['price_date'] for row in _all(conn,
        'SELECT DISTINCT price_date FROM price_bars WHERE ticker = %s AND price_date = ANY(%s)',
        (ticker.upper(), sessions))}
    return [day for day in sessions if day not in found]
