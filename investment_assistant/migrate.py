from __future__ import annotations

import json
import os
import re
from pathlib import Path

from psycopg import sql

from investment_assistant.db import applied_migrations, apply_pending_migrations, close_pools, connect

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
DROP_V2_MIGRATION = "033_drop_v2_tables.sql"
ALLOW_DROP_V2_ENV = "INVESTMENT_ASSISTANT_ALLOW_DROP_V2_TABLES"
_DROP_TABLE = re.compile(r"^\s*DROP\s+TABLE\s+IF\s+EXISTS\s+(\w+)\s*;", re.IGNORECASE | re.MULTILINE)


class DropV2NotConfirmed(RuntimeError):
    """Migration 033 is pending, would delete tables that hold rows, and was not explicitly confirmed."""

    def __init__(self, tables: list[str]):
        self.tables = tables
        shown = "、".join(tables[:10]) + (f" 等共 {len(tables)} 张" if len(tables) > 10 else "")
        super().__init__(
            f"迁移 {DROP_V2_MIGRATION} 会删除 v2 的业务表，删除不可恢复；这些表当前有数据：{shown}。"
            f"没有执行任何迁移。请先按 docs/operations/migrations-and-backup.md 做 pg_dump 备份，"
            f"确认后设置 {ALLOW_DROP_V2_ENV}=1 重新执行。")


def _tables_dropped_by(path: Path) -> list[str]:
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))
    return _DROP_TABLE.findall(text)


def _tables_with_rows(conn, tables: list[str]) -> list[str]:
    populated = []
    for table in tables:
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            continue
        query = sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(table))
        if conn.execute(query).fetchone()[0]:
            populated.append(table)
    return populated


def _refuse_unconfirmed_drop(conn, target: Path) -> None:
    drop_file = target / DROP_V2_MIGRATION
    if not drop_file.exists() or DROP_V2_MIGRATION in applied_migrations(conn):
        return
    populated = _tables_with_rows(conn, _tables_dropped_by(drop_file))
    conn.rollback()
    if populated:
        raise DropV2NotConfirmed(populated)


def run(database_url: str, migrations_dir: str | Path | None = None, *,
        allow_drop_v2_tables: bool = False) -> list[str]:
    target = Path(migrations_dir or DEFAULT_MIGRATIONS_DIR)
    try:
        with connect(database_url) as conn:
            if not allow_drop_v2_tables:
                _refuse_unconfirmed_drop(conn, target)
            return apply_pending_migrations(conn, target)
    finally:
        close_pools()


def main() -> None:
    url = os.environ.get("INVESTMENT_ASSISTANT_DATABASE_URL")
    if not url:
        raise SystemExit("INVESTMENT_ASSISTANT_DATABASE_URL is required to run migrations")
    try:
        applied = run(url, allow_drop_v2_tables=os.environ.get(ALLOW_DROP_V2_ENV) == "1")
    except DropV2NotConfirmed as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps({"applied": applied, "count": len(applied)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
