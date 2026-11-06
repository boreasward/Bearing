"""Feature flags read from the append-only ``feature_flag_changes`` table (migration 031); read-only."""
from __future__ import annotations

from psycopg.rows import dict_row

FLAGS = ('backup_sources',)


def current(conn) -> dict[str, bool]:
    """One latest row per flag, by id; a flag without history is enabled."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT DISTINCT ON (flag) flag, enabled_after FROM feature_flag_changes '
                    'ORDER BY flag, id DESC')
        values = {row['flag']: row['enabled_after'] for row in cur.fetchall()}
    return {flag: values.get(flag, True) for flag in FLAGS}


def frozen(conn) -> dict[str, bool]:
    """A fresh snapshot for initial_state; subsequent changes do not mutate it."""
    return current(conn)
