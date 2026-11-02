"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
from datetime import date, datetime
from typing import Any
from investment_assistant.core.dbutil import _one, _all, _plan_json


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
    'stages-5': ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'accounts',
                 'push', 'retro'),
    'stages-6': ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'portfolio', 'accounts', 'push', 'retro', 'propose'),
}
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
