"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
from datetime import date, datetime
from typing import Any
from investment_assistant.core.dbutil import _one, _all, _plan_json


_DECISION_COLUMNS = ('id, request_id, request_hash, ticker, signal_date, candidate_ids, brief_id, proposed_action, '
                     'final_action, decision, reason, decided_at, supersedes_id, plan_id')
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


def unit_decision_chain(conn, ticker: str, signal_date: date) -> list[dict[str, Any]]:
    """The old decisions of one unit, oldest first, as ``{id, plan_id, decided_at}`` (a retro-2 card needs the plan of
    the decision a fill was attached to, not only the head decision)."""
    return _all(conn, 'SELECT id, plan_id, decided_at FROM signal_decisions WHERE ticker = %s AND signal_date = %s '
                      'ORDER BY decided_at, id', (ticker.upper(), signal_date))
