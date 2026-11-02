"""The roadmap records as a use case (v2.15, spec §8.1, §8.2, §8.5, §12.1, §12.3).

* ``import_dir`` loads the four seed files (``goals / versions / decisions / evidence``, each a YAML list whose
  fields are the columns of spec §11.4) idempotently: the same key and record version with the same content is
  skipped, other content is a conflict and nothing of that import is written.
* ``export_markdown`` / ``view`` / ``version_detail`` / ``decision_detail`` read the latest record versions; the
  Markdown is the pure ``optimize.export`` template.
* ``get_roadmap`` is the ``get_roadmap`` tool of the model roles whose tool set names it. It lives here and reaches
  a run only through the immutable ``ToolContext.bindings`` the optimize host supplies (R13): the analysis layer
  never imports it. Every record is read as of the run's cutoff.
* ``main`` is the guarded command line: ``python -m investment_assistant.optimize.roadmap import --dir DIR`` /
  ``export [--out FILE]``. ``--help`` touches nothing.

Readiness: before migration 031 every entry raises ``Unavailable('migration_031_missing')`` (spec §11.5)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from investment_assistant.analysis.agent.tools import ToolContext, ToolError
from investment_assistant.core import dbutil as core_dbutil
from investment_assistant.core import schema as core_schema
from investment_assistant.core import tx as core_tx
from investment_assistant.core.idempotency import Conflict
from investment_assistant.optimize import export as roadmap_export
from investment_assistant.optimize import repo as optimize_repo

SECTIONS = {'goals': 'roadmap_goals', 'versions': 'roadmap_versions', 'decisions': 'roadmap_decisions',
            'evidence': 'roadmap_evidence'}
FILES = tuple((f'{name}.yaml', name, table) for name, table in SECTIONS.items())
TOOL_SECTIONS = ('goals', 'versions', 'decisions', 'proposals')
TOOL_ITEMS_MAX = 40

VERSION_STATUSES = ('planned', 'in_progress', 'engineering_done', 'user_acceptance_pending', 'done', 'dropped')
EVIDENCE_KINDS = ('manual', 'scheduled', 'user_confirmed')
# the fields a seed row may carry: the columns minus bookkeeping the database assigns
_BOOKKEEPING = frozenset({'supersedes', 'created_at'})
_REQUIRED = {'roadmap_goals': ('body',), 'roadmap_versions': ('title', 'status'),
             'roadmap_decisions': ('decided_on', 'question', 'decision'),
             'roadmap_evidence': ('version_id', 'kind', 'body')}
_LISTS = frozenset({'depends_on', 'version_ids'})
_DATES = frozenset({'decided_on', 'observed_on'})
# not in a seed row's content: the key and the record version identify it
_IDENTITY = frozenset({'version'})


def ensure_ready(conn) -> None:
    if not core_schema.five_layers_ready(conn):
        raise core_tx.Unavailable('migration_031_missing')


# ---------------------------------------------------------------- reading (natural order, jsonable)

def natural_key(value: Any) -> list:
    """``v2.9`` before ``v2.10``: digits compare as numbers (the split alternates text / number positionally)."""
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', str(value))]


def latest(conn, table: str, *, cutoff_at=None) -> list[dict]:
    key = optimize_repo.record_key(table)
    rows = optimize_repo.latest_records(conn, table, cutoff_at=cutoff_at)
    return sorted(rows, key=lambda row: natural_key(row[key]))


def view(*, now: datetime) -> dict:
    """``GET /api/roadmap``: the latest version of every goal, version, decision and evidence record."""
    with core_tx.read_only_tx() as conn:
        ensure_ready(conn)
        out = {name: latest(conn, table) for name, table in SECTIONS.items()}
    return core_tx._jsonable({**out, 'exported_at': now.isoformat()})


def brief_state(conn, version_id: str, history: list[dict] | None = None) -> tuple[dict | None, str]:
    """``(latest brief row | None, status)``: ``not_requested`` without a proposal behind the version, ``pending``
    while an approved proposal has no brief row yet, otherwise the last row's ``ok`` / ``error``."""
    briefs = optimize_repo.briefs_for(conn, version_id)
    if briefs:
        return briefs[-1], briefs[-1]['status']
    history = history if history is not None else optimize_repo.record_history(conn, 'roadmap_versions', version_id)
    proposal_id = next((row['proposal_id'] for row in reversed(history) if row.get('proposal_id') is not None), None)
    if proposal_id is None:
        return None, 'not_requested'
    proposal = optimize_repo.proposal_by_id(conn, proposal_id)
    return None, 'pending' if proposal is not None and proposal['status'] == 'approved' else 'not_requested'


def version_detail(version_id: str) -> dict | None:
    """``{version, history, brief, brief_status}`` or None for an unknown id (UI1)."""
    with core_tx.read_only_tx() as conn:
        ensure_ready(conn)
        history = optimize_repo.record_history(conn, 'roadmap_versions', version_id)
        if not history:
            return None
        brief, status = brief_state(conn, version_id, history)
    return core_tx._jsonable({'version': history[-1], 'history': history, 'brief': brief, 'brief_status': status})


def decision_detail(decision_no: int) -> dict | None:
    with core_tx.read_only_tx() as conn:
        ensure_ready(conn)
        history = optimize_repo.record_history(conn, 'roadmap_decisions', decision_no)
    if not history:
        return None
    return core_tx._jsonable({'decision': history[-1], 'history': history})


def export_markdown(*, now: datetime) -> str:
    """The fixed template over the latest records and the open proposals (``now`` is part of the contract: the
    export is a function of the stored records only, so the same database gives the same text)."""
    with core_tx.read_only_tx() as conn:
        ensure_ready(conn)
        records = {name: latest(conn, table) for name, table in SECTIONS.items()}
        open_proposals = sorted(optimize_repo.list_proposals(conn, status='proposed'), key=lambda row: row['id'])
    drop = ('id', 'supersedes', 'created_at')
    return roadmap_export.render_markdown(
        goals=[{k: v for k, v in row.items() if k not in drop} for row in records['goals']],
        versions=[{k: v for k, v in row.items() if k not in drop} for row in records['versions']],
        decisions=[{k: v for k, v in row.items() if k not in drop} for row in records['decisions']],
        evidence=[{k: v for k, v in row.items() if k not in drop} for row in records['evidence']],
        proposals=[{k: v for k, v in row.items() if k not in ('run_id', 'agent_run_id', 'request_id')}
                   for row in open_proposals])


# ---------------------------------------------------------------- import

def _invalid(file: str, detail: str) -> ValueError:
    return ValueError(f'roadmap_import_invalid: {file}: {detail}')


def _normalise(table: str, file: str, index: int, item: Any) -> dict:
    """One seed row as the record it stands for (defaults filled, types checked, unknown fields refused)."""
    where = f'#{index + 1}'
    if not isinstance(item, dict):
        raise _invalid(file, f'{where} is not a mapping')
    key = optimize_repo.record_key(table)
    columns = set(optimize_repo.record_columns(table)) - _BOOKKEEPING
    unknown = sorted(set(item) - columns)
    if unknown:
        raise _invalid(file, f"{where} unknown field {', '.join(map(str, unknown))}")
    row: dict[str, Any] = {name: item.get(name) for name in columns}
    row['version'] = 1 if item.get('version') is None else item['version']
    if isinstance(row['version'], bool) or not isinstance(row['version'], int) or row['version'] < 1:
        raise _invalid(file, f'{where} version must be a positive integer')
    if table == 'roadmap_decisions':
        if isinstance(row[key], bool) or not isinstance(row[key], int) or row[key] < 1:
            raise _invalid(file, f'{where} {key} must be a positive integer')
    elif not isinstance(row[key], str) or not row[key].strip():
        raise _invalid(file, f'{where} {key} is required')
    for name in _REQUIRED[table]:
        if row[name] is None or (isinstance(row[name], str) and not row[name].strip()):
            raise _invalid(file, f'{where} {name} is required')
    for name in columns - _LISTS - _DATES - {key, 'version', 'proposal_id'}:
        if row[name] is not None and not isinstance(row[name], str):
            raise _invalid(file, f'{where} {name} must be text')
    for name in _LISTS & columns:
        value = [] if row[name] is None else row[name]
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise _invalid(file, f'{where} {name} must be a list of text')
        row[name] = list(value)
    for name in _DATES & columns:
        value = row[name]
        if isinstance(value, datetime):
            raise _invalid(file, f'{where} {name} must be a date')
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                raise _invalid(file, f'{where} {name} must be YYYY-MM-DD') from None
        if value is not None and not isinstance(value, date):
            raise _invalid(file, f'{where} {name} must be a date')
        row[name] = value
    if row.get('proposal_id') is not None and (isinstance(row['proposal_id'], bool)
                                               or not isinstance(row['proposal_id'], int)):
        raise _invalid(file, f'{where} proposal_id must be an integer')
    if table == 'roadmap_versions' and row['status'] not in VERSION_STATUSES:
        raise _invalid(file, f"{where} status must be one of {', '.join(VERSION_STATUSES)}")
    if table == 'roadmap_evidence' and row['kind'] not in EVIDENCE_KINDS:
        raise _invalid(file, f"{where} kind must be one of {', '.join(EVIDENCE_KINDS)}")
    return row


def _load(directory: Path) -> dict[str, list[dict]]:
    loaded: dict[str, list[dict]] = {}
    for file, name, table in FILES:
        path = directory / file
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise _invalid(file, 'file is missing') from None
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise _invalid(file, f'cannot be read ({type(exc).__name__})') from None
        if data is None:
            data = []
        if not isinstance(data, list):
            raise _invalid(file, 'must be a list')
        key = optimize_repo.record_key(table)
        rows = [_normalise(table, file, index, item) for index, item in enumerate(data)]
        seen = set()
        for row in rows:
            identity = (row[key], row['version'])
            if identity in seen:
                raise _invalid(file, f'{key} {row[key]} (version {row["version"]}) appears twice')
            seen.add(identity)
        loaded[name] = rows
    return loaded


def import_dir(path) -> dict[str, dict[str, int]]:
    """Load ``goals / versions / decisions / evidence`` ``.yaml`` from ``path`` (spec §8.2): ``{section: {inserted,
    skipped}}``. Same key + version + content → skipped; same key + version with other content → ``Conflict``
    naming the file, with nothing of the import written (one transaction, the plan is checked before any insert)."""
    loaded = _load(Path(path))
    with core_tx._tx() as conn:
        ensure_ready(conn)
        core_dbutil.lock(conn, 'roadmap:import')
        plan: list[tuple[str, str, dict, bool]] = []          # (section, table, row, exists)
        conflicts: list[str] = []
        for file, name, table in FILES:
            key = optimize_repo.record_key(table)
            compared = set(optimize_repo.record_columns(table)) - _BOOKKEEPING - {key, 'version'}
            for row in loaded[name]:
                stored = next((old for old in optimize_repo.record_history(conn, table, row[key])
                               if old['version'] == row['version']), None)
                if stored is None:
                    plan.append((name, table, row, False))
                elif all(stored.get(column) == row.get(column) for column in compared):
                    plan.append((name, table, row, True))
                else:
                    differing = sorted(c for c in compared if stored.get(c) != row.get(c))
                    conflicts.append(f'{file}: {row[key]} v{row["version"]} differs in {", ".join(differing)}')
        if conflicts:
            raise Conflict('roadmap_import_conflict: ' + '; '.join(conflicts[:5]))
        counts = {name: {'inserted': 0, 'skipped': 0} for _, name, _ in FILES}
        for name, table, row, exists in plan:
            if exists:
                counts[name]['skipped'] += 1
            else:
                optimize_repo.insert_roadmap_record(conn, table, row)
                counts[name]['inserted'] += 1
    return counts


# ---------------------------------------------------------------- the get_roadmap tool (spec §8.5)

def _ref(table: str, key: Any, version: int) -> str:
    return f'roadmap:{table}:{key}@{version}'


def _matches(query: str | None, *fields: Any) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(needle in str(field).casefold() for field in fields if field is not None)


def _known_at(row: dict, cutoff_at: datetime) -> dict:
    """A proposal as it was at the cutoff: a decision made later is not yet known."""
    if row['decided_at'] is not None and row['decided_at'] > cutoff_at:
        return {**row, 'status': 'proposed', 'decided_at': None, 'decision_reason': None, 'request_id': None}
    return row


def get_roadmap(ctx: ToolContext, section: str, query: str | None = None) -> dict:
    """``get_roadmap(section, query)``: the latest record versions created at or before ``ctx.cutoff_at``, each with
    a ``ref`` (``roadmap:<table>:<key>@<version>``) that can be cited. ``versions`` filters by ``version_id``,
    ``decisions`` / ``goals`` / ``proposals`` by keyword. At most ``TOOL_ITEMS_MAX`` items, ``truncated`` says so."""
    if section not in TOOL_SECTIONS:
        raise ToolError(f"section 必须是 {' / '.join(TOOL_SECTIONS)} 之一")
    if query is not None and not isinstance(query, str):
        raise ToolError('query 必须是文本')
    cutoff_at = ctx.cutoff_at

    def read(conn):
        if section == 'proposals':
            rows = [row for row in sorted(optimize_repo.list_proposals(conn), key=lambda r: r['id'])
                    if row['created_at'] <= cutoff_at]
            rows = [_known_at(row, cutoff_at) for row in rows]
            return [{**row, 'ref': _ref('roadmap_proposals', row['id'], 1)} for row in rows
                    if _matches(query, row['title'], row['change'], row['rationale'])]
        table = SECTIONS[section]
        key = optimize_repo.record_key(table)
        rows = latest(conn, table, cutoff_at=cutoff_at)
        if section == 'versions':
            rows = [row for row in rows if not query or row[key].casefold() == query.strip().casefold()]
        elif section == 'decisions':
            rows = [row for row in rows if _matches(query, row['question'], row['decision'], row['quote'])]
        else:
            rows = [row for row in rows if _matches(query, row[key], row['body'])]
        return [{**row, 'ref': _ref(table, row[key], row['version'])} for row in rows]

    items = ctx.read(read)
    return core_tx._jsonable({'section': section, 'query': query, 'cutoff_at': cutoff_at.isoformat(),
                              'total': len(items), 'truncated': len(items) > TOOL_ITEMS_MAX,
                              'items': items[:TOOL_ITEMS_MAX]})


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.optimize.roadmap',
                                     description='系统内 roadmap：导入种子文件、导出 Markdown。')
    commands = parser.add_subparsers(dest='command', required=True)
    load = commands.add_parser('import', help='从目录导入 goals / versions / decisions / evidence 的 YAML（幂等）')
    load.add_argument('--dir', required=True, help='种子目录，例如 config/roadmap')
    dump = commands.add_parser('export', help='导出 Markdown（与 GET /api/roadmap/export.md 同一函数）')
    dump.add_argument('--out', help='输出文件；缺省打印到标准输出')
    args = parser.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv()
    try:
        if args.command == 'import':
            print(json.dumps(import_dir(args.dir), ensure_ascii=False))
            return 0
        from datetime import UTC

        text = export_markdown(now=datetime.now(UTC))
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding='utf-8')
            print(f'已写入 {out}')
        else:
            sys.stdout.write(text)
        return 0
    except (ValueError, Conflict, LookupError, core_tx.Unavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
