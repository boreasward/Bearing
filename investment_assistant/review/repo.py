"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo
from investment_assistant.core.dbutil import _one, _all, _canonical, _plan_json, _account_scope

from investment_assistant.data import repo as data_repo
from investment_assistant.execution import repo as execution_repo, repo_legacy
from investment_assistant.execution.repo import _trade_columns

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


def review_card_refs_by_ids(conn, ids) -> list[dict[str, Any]]:
    """Needs migration 027."""
    return _all(conn, 'SELECT id, ticker, subject, end_kind FROM review_cards WHERE id = ANY(%s) ORDER BY id',
                (list(ids),))


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


_CARD_COLUMNS = ('id, event_key, subject, ticker, signal_date, buy_trade_id, end_kind, end_session, '
                 'retro_version, facts, detected_run_id, created_at')
_CARD_DRAFT_COLUMNS = 'id, card_id, attempt, trigger, agent_run_id, status, reason, output, created_at'
_CARD_VERDICT_COLUMNS = 'id, card_id, request_id, request_hash, draft_id, note, created_at'
_KNOWLEDGE_ITEM_COLUMNS = 'id, origin, source_card_id, source_discussion_id, created_at'
_KNOWLEDGE_DRAFT_COLUMNS = ('id, origin, card_draft_id, lesson_index, discussion_turn_id, target_knowledge_id, '
                            'target_version, title, body, scope, counter_examples, concepts, tickers, refs, '
                            'created_at')
_CITATION_COLUMNS = 'id, agent_run_id, plan_id, review_id, knowledge_id, version, field, call_seq, created_at'
_DISCUSSION_COLUMNS = ('id, request_id, request_hash, context_kind, ticker, signal_date, plan_id, card_id, '
                       'created_at')
_TURN_COLUMNS = ('id, discussion_id, seq, role, request_id, request_hash, body, attachment, run_id, reply_to, '
                 'status, reason, refs, detail, agent_run_id, cutoff_at, created_at')


_NY = ZoneInfo('America/New_York')


def _ny_date(moment: datetime) -> date:
    return moment.astimezone(_NY).date()


def _open_before(conn, ticker: str, since: date) -> bool:
    """True when the ticker still has shares after the live trades executed before ``since`` (New York date).
    A running quantity with the splits applied the way ``positions.build`` does; it does not match lots to
    units, so it errs towards True (the caller then lets ``retro.end_event`` decide). The real account's trades
    only: retro cards are about what you actually did (spec D4/D18)."""
    pending = data_repo.split_events(conn, ticker)
    _, scope, params = _account_scope(conn)
    quantity = Decimal(0)
    for side, qty, day in conn.execute(
            "SELECT side, quantity, (executed_at AT TIME ZONE 'America/New_York')::date FROM trades "
            "WHERE ticker = %s AND voided_at IS NULL AND (executed_at AT TIME ZONE 'America/New_York')::date < %s "
            f'{scope} ORDER BY executed_at, id',
            [ticker.upper(), since, *params]).fetchall():
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
    ready, scope, params = _account_scope(conn)
    columns = _trade_columns(ready)
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
        buys = _all(conn, f"SELECT {columns} FROM trades WHERE side = 'buy' AND voided_at IS NULL "
                          f'AND decision_id = ANY(%s){scope} ORDER BY executed_at, id', [decision_ids, *params])
        if buys:
            if all(_ny_date(b['executed_at']) < since for b in buys) and closed_before(ticker):
                continue
        elif conn.execute("SELECT 1 FROM review_cards WHERE subject = 'unit' AND ticker = %s AND signal_date = %s",
                          (ticker, signal_date)).fetchone():
            continue
        subjects.append({'kind': 'unit', 'ticker': ticker, 'signal_date': signal_date, 'buy_trade_id': None,
                          'decision': repo_legacy.decision_head(conn, ticker, signal_date, timeline=True),
                         'decision_ids': decision_ids, 'decisions': chain, 'buys': buys,
                          'plans': execution_repo.plans_for_unit(conn, ticker, signal_date),
                          'reviews': execution_repo.chain_reviews(conn, ticker, signal_date)})
    for buy in _all(conn, f"SELECT {columns} FROM trades WHERE side = 'buy' AND decision_id IS NULL "
                          "AND voided_at IS NULL AND (executed_at AT TIME ZONE 'America/New_York')::date <= %s "
                          f'{scope} ORDER BY executed_at, id', [until, *params]):
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

def retro_subject_keys(conn, *, until: date) -> dict[str, Any]:
    """The subjects a `retro-2` card may be about (v2.9 spec D29), as keys; the caller reads the rest.

    ``units``: ``(ticker, signal_date)`` of every unit or chain that has a live buy executed (New York date) on or
    before ``until`` — linked through ``trades.plan_id`` (a fill linked to the baseline of a unit merged into a chain
    counts for the chain) or, for old data, through a decision of the unit — or that carries a live plan note (a note
    on a chain, or on a rule / own plan, which counts for the plan's unit). ``trades``: the live buys with neither a
    plan nor a decision, oldest first (the plan-less trades)."""
    ready, scope, params = _account_scope(conn, alias='t')
    day = "(t.executed_at AT TIME ZONE 'America/New_York')::date"
    linked = _all(conn,
                  'SELECT DISTINCT t.ticker, CASE '
                  'WHEN EXISTS (SELECT 1 FROM agent_plan_versions v WHERE v.ticker = p.ticker '
                  'AND v.signal_date = p.signal_date) THEN p.signal_date '
                  'ELSE COALESCE((SELECT r.chain_signal_date FROM chain_retriggers r WHERE r.ticker = p.ticker '
                  'AND r.unit_signal_date = p.signal_date), p.signal_date) END AS signal_date '
                  'FROM trades t JOIN trade_plans p ON p.id = t.plan_id '
                  f"WHERE t.side = 'buy' AND t.voided_at IS NULL{scope} AND {day} <= %s", [*params, until])
    legacy = _all(conn,
                  'SELECT DISTINCT d.ticker, d.signal_date FROM signal_decisions d JOIN trades t '
                  "ON t.decision_id IN (SELECT x.id FROM signal_decisions x WHERE x.ticker = d.ticker "
                  "AND x.signal_date = d.signal_date) WHERE t.side = 'buy' AND t.voided_at IS NULL "
                  f"{scope} AND d.signal_date <= %s AND {day} <= %s", [*params, until, until])
    noted = _all(conn,
                 'SELECT DISTINCT n.ticker, COALESCE(n.chain_signal_date, p.signal_date) AS signal_date '
                 'FROM plan_notes n LEFT JOIN trade_plans p ON p.id = n.plan_id '
                 "WHERE n.kind = 'note' AND NOT EXISTS (SELECT 1 FROM plan_notes s WHERE s.supersedes_id = n.id "
                 "AND s.kind = 'retract') AND COALESCE(n.chain_signal_date, p.signal_date) <= %s", (until,))
    plan_less = _all(conn, f"SELECT {_trade_columns(ready)} FROM trades t WHERE t.side = 'buy' "
                           f'AND t.voided_at IS NULL AND t.decision_id IS NULL AND t.plan_id IS NULL{scope} '
                           f'AND {day} <= %s ORDER BY t.executed_at, t.id', [*params, until])
    keys = sorted({(r['ticker'], r['signal_date']) for r in (*linked, *legacy, *noted)},
                  key=lambda k: (k[1], k[0]))
    return {'units': keys, 'trades': plan_less}
