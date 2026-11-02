"""Layer-owned SQL. Callers own transactions."""
from __future__ import annotations
from typing import Any
from psycopg import sql
from investment_assistant.core.dbutil import _one, _all, _canonical
from investment_assistant.core.idempotency import Conflict


_BACKCHECK_COLUMNS = 'id, calc_version, params, stats, created_at'


def insert_backcheck(conn, calc_version: str, params: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Freeze one backcheck run; rows are never rewritten."""
    return _one(conn, 'INSERT INTO rule_backchecks (calc_version, params, stats) '
                      f'VALUES (%s, %s::jsonb, %s::jsonb) RETURNING {_BACKCHECK_COLUMNS}',
                (calc_version, _canonical(params), _canonical(stats)))


def list_backchecks(conn, limit: int) -> list[dict[str, Any]]:
    return _all(conn, f'SELECT {_BACKCHECK_COLUMNS} FROM rule_backchecks ORDER BY created_at DESC LIMIT %s',
                (limit,))


# Identifiers are selected only from this closed schema, never interpolated from a caller.
_RECORDS = {
    'roadmap_goals': ('goal_id', ('goal_id', 'version', 'supersedes', 'body', 'created_at')),
    'roadmap_versions': ('version_id', ('version_id', 'version', 'supersedes', 'title', 'theme', 'scope',
        'depends_on', 'status', 'exit_conditions', 'spec_path', 'plan_path', 'tag', 'proposal_id', 'created_at')),
    'roadmap_decisions': ('decision_no', ('decision_no', 'version', 'supersedes', 'decided_on', 'question',
        'decision', 'quote', 'version_ids', 'created_at')),
    'roadmap_evidence': ('evidence_id', ('evidence_id', 'version', 'version_id', 'kind', 'body', 'observed_on',
        'source_path', 'created_at')),
}
_PROPOSAL_COLUMNS = ('run_id', 'agent_run_id', 'source', 'title', 'change', 'rationale', 'evidence_refs',
                     'expected', 'acceptance', 'status', 'request_id', 'created_at')
_BRIEF_COLUMNS = ('version_id', 'proposal_id', 'agent_run_id', 'body', 'status', 'reason', 'created_at')


def _record_schema(table):
    if table not in _RECORDS:
        raise ValueError('roadmap_record_table')
    return _RECORDS[table]


def _insert(conn, table, columns, row):
    if not row or set(row) - set(columns):
        raise ValueError('roadmap_record_fields')
    names = [name for name in columns if name in row]
    statement = sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING RETURNING *').format(
        sql.Identifier(table), sql.SQL(', ').join(map(sql.Identifier, names)),
        sql.SQL(', ').join(sql.Placeholder() for _ in names))
    saved = _one(conn, statement, [row[name] for name in names])
    if saved is None:
        # DO NOTHING leaves the caller's transaction usable after the application conflict.
        raise Conflict('roadmap_record_conflict')
    return saved


def insert_roadmap_record(conn, table, row):
    """Append one revision to one of four tables. UNIQUE collisions are never overwrites."""
    _, columns = _record_schema(table)
    return _insert(conn, table, columns, row)


def latest_records(conn, table, *, cutoff_at=None) -> list[dict]:
    key, _ = _record_schema(table)
    where = sql.SQL('WHERE created_at <= %s') if cutoff_at is not None else sql.SQL('')
    return _all(conn, sql.SQL('SELECT DISTINCT ON ({key}) * FROM {table} {where} '
        'ORDER BY {key}, version DESC, id DESC').format(key=sql.Identifier(key), table=sql.Identifier(table),
                                                     where=where),
        (cutoff_at,) if cutoff_at is not None else ())


def record_history(conn, table, key):
    name, _ = _record_schema(table)
    return _all(conn, sql.SQL('SELECT * FROM {} WHERE {} = %s ORDER BY version, id').format(
        sql.Identifier(table), sql.Identifier(name)), (key,))


def insert_proposal(conn, row):
    if row.get('status', 'proposed') != 'proposed':
        raise ValueError('proposal_initial_status')
    return _insert(conn, 'roadmap_proposals', _PROPOSAL_COLUMNS, row)


def decide_proposal(conn, id, *, decision, reason, decided_at) -> bool:
    if decision not in {'approved', 'rejected', 'superseded'}:
        raise ValueError('proposal_decision')
    if not isinstance(reason, str) or not reason.strip() or decided_at is None:
        raise ValueError('proposal_decision_reason')
    return conn.execute('UPDATE roadmap_proposals SET status = %s, decision_reason = %s, decided_at = %s '
        "WHERE status = 'proposed' AND id = %s", (decision, reason.strip(), decided_at, id)).rowcount == 1


def list_proposals(conn, *, status=None):
    where = ' WHERE status = %s' if status is not None else ''
    return _all(conn, 'SELECT * FROM roadmap_proposals' + where + ' ORDER BY created_at DESC, id DESC',
                (status,) if status is not None else ())


def insert_brief(conn, row):
    return _insert(conn, 'roadmap_briefs', _BRIEF_COLUMNS, row)


def briefs_for(conn, version_id):
    return _all(conn, 'SELECT * FROM roadmap_briefs WHERE version_id = %s ORDER BY created_at, id', (version_id,))


def proposals_without_changes(conn) -> list[int]:
    """Clean proposed IDs (not violations); compare these with all proposed IDs in workflow.check."""
    return [row['id'] for row in _all(conn,
        "SELECT p.id FROM roadmap_proposals p WHERE p.status = 'proposed' "
        'AND NOT EXISTS (SELECT 1 FROM roadmap_versions v WHERE v.proposal_id = p.id) '
        'AND NOT EXISTS (SELECT 1 FROM roadmap_briefs b WHERE b.proposal_id = p.id) ORDER BY p.id', ())]


def fetch_failures_since(conn, since):
    """3A aggregates. Target counts by final error kind; run counts in the NULL-kind bucket.

    Missing/truncated target detail is accounted for as unknown, using the recorded
    target_count - ok_count. A multi-kind run contributes only once to partial/failed.
    Backup use means a successfully stored Stooq/Nasdaq target, not merely attempted fallback.
    """
    return _all(conn, '''WITH runs AS (
            SELECT * FROM data_fetch_runs WHERE finished_at >= %s
        ), targets AS (
            SELECT r.id, r.family, t.value FROM runs r CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(r.detail->'targets') = 'array' THEN r.detail->'targets'
                     ELSE '[]'::jsonb END) t
        ), counts AS (
            SELECT family, COALESCE(NULLIF(value->>'error_kind', ''), 'unknown') AS error_kind,
                count(*) AS failed_targets, 0::bigint AS backup_used, 0::bigint AS partial, 0::bigint AS failed
            FROM targets WHERE value->>'ok' = 'false' AND value->>'target' IS DISTINCT FROM '*'
            GROUP BY family, COALESCE(NULLIF(value->>'error_kind', ''), 'unknown')
            UNION ALL
            SELECT r.family, 'unknown',
                GREATEST(r.target_count - r.ok_count - (SELECT count(*) FROM targets t
                    WHERE t.id = r.id AND t.value->>'ok' = 'false'
                    AND t.value->>'target' IS DISTINCT FROM '*'), 0), 0, 0, 0
            FROM runs r WHERE r.status IN ('partial', 'failed')
            UNION ALL
            SELECT family, NULL, 0, count(*) FILTER (WHERE value->>'ok' = 'true'
                AND value->>'source_used' IN ('stooq', 'nasdaq')), 0, 0 FROM targets GROUP BY family
            UNION ALL
            SELECT family, NULL, 0, 0, count(*) FILTER (WHERE status = 'partial'),
                count(*) FILTER (WHERE status = 'failed') FROM runs GROUP BY family
        ) SELECT family, error_kind, sum(failed_targets)::bigint AS failed_targets,
            sum(backup_used)::bigint AS backup_used, sum(partial)::bigint AS partial, sum(failed)::bigint AS failed
          FROM counts GROUP BY family, error_kind
          HAVING sum(failed_targets + backup_used + partial + failed) > 0
          ORDER BY family, error_kind NULLS FIRST''', (since,))


def agent_reasons_since(conn, since):
    """Rejected/error attempts and deferred requests use the same 3A mode/reason/count stream."""
    return _all(conn, '''SELECT mode, reason, count(*) AS count FROM (
            SELECT mode, reason FROM agent_runs WHERE finished_at >= %s AND status IN ('rejected', 'error')
            UNION ALL
            SELECT 'data_request', 'deferred' FROM data_requests
            WHERE status = 'deferred' AND COALESCE(updated_at, requested_at) >= %s
        ) reasons GROUP BY mode, reason ORDER BY mode, reason''', (since, since))


def research_blocked_since(conn, since):
    """Accept the research payload directly or in its checked status/output envelope."""
    return _all(conn, '''SELECT item->>'why' AS why, count(*) AS count
        FROM agent_runs r CROSS JOIN LATERAL jsonb_array_elements(CASE
            WHEN jsonb_typeof(r.output#>'{research,blocked}') = 'array' THEN r.output#>'{research,blocked}'
            WHEN jsonb_typeof(r.output#>'{research,output,blocked}') = 'array'
                THEN r.output#>'{research,output,blocked}'
            ELSE '[]'::jsonb END) item
        WHERE r.finished_at >= %s AND NULLIF(btrim(item->>'why'), '') IS NOT NULL
        GROUP BY item->>'why' ORDER BY why''', (since,))


def counter_adoption_since(conn, since):
    """D35's observable run-level proxy: counter present and a successful proposer revision follows.

    This does not infer per-item causal adoption: legacy counter JSON has no such marker.
    Only an actual superseding plan from mode=revise counts, never an unrelated later run.
    """
    return _all(conn, '''WITH countered AS (
            SELECT r.id FROM agent_runs r WHERE r.finished_at >= %s AND (
                CASE WHEN jsonb_typeof(r.counter->'counter') = 'array'
                    THEN jsonb_array_length(r.counter->'counter') ELSE 0 END > 0 OR
                CASE WHEN jsonb_typeof(r.counter->'risks') = 'array'
                    THEN jsonb_array_length(r.counter->'risks') ELSE 0 END > 0)
        ) SELECT count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM agent_plan_versions old
            JOIN agent_plan_versions next ON next.supersedes_plan_id = old.plan_id
            JOIN agent_runs revised ON revised.id = next.agent_run_id
            WHERE old.agent_run_id = c.id AND revised.mode = 'revise' AND revised.status = 'ok'
        )) AS adopted, count(*) AS total FROM countered c''', (since,))


def lesson_repeats_since(conn, since):
    """Count independent lesson observations, not the card→knowledge copy as a second occurrence."""
    return _all(conn, '''WITH lessons AS (
            SELECT lesson->>'scope' AS scope FROM review_card_drafts d
            CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(d.output->'lessons') = 'array'
                THEN d.output->'lessons' ELSE '[]'::jsonb END) lesson
            WHERE d.status = 'ok' AND d.created_at >= %s
            UNION ALL
            SELECT k.scope FROM knowledge_drafts k WHERE k.created_at >= %s AND NOT EXISTS (
                SELECT 1 FROM review_card_drafts d WHERE d.id = k.card_draft_id AND d.status = 'ok'
                AND NULLIF(d.output->'lessons'->k.lesson_index->>'scope', '') IS NOT NULL)
        ) SELECT scope, count(*) AS count FROM lessons WHERE NULLIF(btrim(scope), '') IS NOT NULL
        GROUP BY scope HAVING count(*) >= 2 ORDER BY scope''', (since, since))


def stale_versions(conn, *, days=30):
    """Latest route revision and elapsed days at transaction time; retain the >30 fractional boundary."""
    if type(days) is not int or days < 0:
        raise ValueError('stale_version_days')
    return _all(conn, '''WITH latest AS (
            SELECT DISTINCT ON (version_id) * FROM roadmap_versions
            ORDER BY version_id, version DESC, id DESC
        ) SELECT latest.*, (extract(epoch FROM (CURRENT_TIMESTAMP - created_at)) / 86400)::double precision AS days_pending
        FROM latest WHERE status = 'user_acceptance_pending'
        AND created_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day') ORDER BY version_id''', (days,))
