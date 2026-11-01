"""Read-only DB CLI pending the workflow/check and derivation CLI split."""
from __future__ import annotations
import investment_assistant.core.schema as core_schema
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.repo as workflow_repo

import argparse
import sys
import unicodedata
from datetime import UTC, date, datetime, time as dtime
from typing import Any, Callable

from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import NY, SH
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.derivation.chain import derive_plan
from investment_assistant.views import derivation as derivation_view
from investment_assistant.core import stages as stages_module
from investment_assistant.workflow import graph as workflow

PLAN_KINDS = ('rule', 'own', 'agent')
RUN_STAGE_COUNT = {'stages-1': 7, 'stages-2': 8, 'stages-3': 9, 'stages-4': 10, 'stages-5': 11}


def session_window(session: date) -> tuple[datetime, datetime]:
    """[the session's New York 16:00, the next trading day's New York 16:00) as UTC instants."""
    start = datetime.combine(session, dtime(16, 0), tzinfo=NY)
    end = datetime.combine(add_trading_days(session, 1), dtime(16, 0), tzinfo=NY)
    return start.astimezone(UTC), end.astimezone(UTC)


def _beijing(moment: datetime | None) -> str:
    return moment.astimezone(SH).strftime('%Y-%m-%d %H:%M') if moment is not None else '-'


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)


def _table(headers: list[str], rows: list[list[str]], indent: str = '') -> list[str]:
    """Plain-text table, columns padded to the widest cell (CJK counted double-width)."""
    widths = [max(_width(cell) for cell in column) for column in zip(headers, *rows)]
    return [indent + '  '.join(cell + ' ' * (w - _width(cell)) for cell, w in zip(row, widths)).rstrip()
            for row in [headers, *rows]]


def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'日期格式应为 YYYY-MM-DD：{text}') from None


def _check(args: argparse.Namespace, out: Callable[[str], None]) -> int:

    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with core_tx.read_only_tx() as conn:
        for plan in execution_repo.plans_since(conn, args.since):
            if (args.until is not None and plan['signal_date'] > args.until) \
                    or (args.kind is not None and plan['kind'] != args.kind):
                continue
            results.append((plan, derive_plan(plan, derivation_view.load_rows(conn, plan))['consistency']))
    rows = [[str(plan['id']), plan['ticker'], plan['signal_date'].isoformat(), plan['kind'], plan['status'],
             consistency['status'], _beijing(plan['created_at'])] for plan, consistency in results]
    if rows:
        for line in _table(['plan_id', '标的', '信号日', '种类', '状态', '一致性', '生成时间（北京）'], rows):
            out(line)
        for plan, consistency in results:
            tag = f"  {str(plan['id'])[:8]} {plan['ticker']}"
            for item in consistency['mismatches']:
                out(f"{tag} 差异 {item['key']}：推导 {item['result']} ≠ 已存 {item['stored']}")
            for item in consistency['missing']:
                out(f'{tag} 缺失 {item}')
    count = lambda status: sum(c['status'] == status for _, c in results)   # noqa: E731
    failing = sum(p['status'] == 'ok' and c['status'] != 'consistent' for p, c in results)
    out(f"汇总：{len(results)} 份，consistent {count('consistent')}，mismatch {count('mismatch')}，"
        f"incomplete {count('incomplete')}；status=ok 且不一致 {failing} 份 → {'不通过' if failing else '通过'}")
    return 1 if failing else 0


def _stage_derivation(row: dict[str, Any]) -> str:
    found = (row.get('detail') or {}).get('derivation')
    if not found:
        return '-'
    return f"{found['checked']} 份，mismatch {found['mismatch']}，incomplete {found['incomplete']}"


def _attempt_problems(rows: list[dict[str, Any]]) -> list[str]:
    """Per-stage attempts are consecutive and only failed/upstream-failed attempts can be retried."""
    by_stage: dict[str, dict[int, dict[str, Any]]] = {}
    for row in rows:
        by_stage.setdefault(row['stage'], {})[int(row.get('attempt') or 1)] = row
    problems = []
    for stage, attempts in by_stage.items():
        numbers = sorted(attempts)
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(f"{stage} 的 attempt 不连续：{', '.join(str(n) for n in numbers)}")
            continue
        for n in numbers[1:]:
            before = attempts[n - 1]
            if not (before['status'] == 'failed'
                    or (before['status'] == 'skipped' and before.get('reason') == 'upstream_failed')):
                problems.append(f"{stage} 的 attempt {n} 不该存在：上一次是 {before['status']}（已完成的节点不重跑）")
    return problems


def _stages(args: argparse.Namespace, out: Callable[[str], None]) -> int:

    start, end = session_window(args.session)
    with core_tx.read_only_tx() as conn:
        ready = core_schema.stage_runs_ready(conn)
        reports = workflow_repo.workbench_reports_between(conn, start, end)
        runs: dict[str, list[dict[str, Any]]] = {}
        for row in workflow_repo.stage_runs_for_session(conn, args.session) if ready else []:
            runs.setdefault(row['run_id'], []).append(row)
        no_evidence = []
        for report in reports:
            if report['run_id'] in runs:
                continue
            rows = workflow_repo.stage_runs_for_run(conn, report['run_id']) if ready else []
            if rows:
                runs[report['run_id']] = rows
            else:
                no_evidence.append(report)
        if ready and core_schema.daily_ready(conn):
            runs = {run_id: workflow_repo.stage_rows_for_run(conn, run_id) for run_id in runs}
        reported = workflow_repo.workbench_run_ids(conn, list(runs))
    if not ready:
        out('迁移 025 未执行，暂无阶段证据')
    complete = incomplete = unreported = 0
    for run_id, all_rows in runs.items():
        rows = list(workflow.effective_rows(all_rows).values())
        have = {row['stage'] for row in rows}
        versions = sorted({row['stages_version'] for row in rows})
        version = versions[0] if len(versions) == 1 else None
        expected = stages_module.STAGE_IDS_BY_VERSION.get(version) if version is not None else None
        missing = [stage for stage in expected if stage not in have] if expected is not None else []
        problems = _attempt_problems(all_rows)
        ok = expected is not None and not missing and not problems and len(rows) == RUN_STAGE_COUNT[version]
        complete += ok
        incomplete += not ok
        unreported += run_id not in reported
        first = all_rows[0]
        extra = f'（共 {len(all_rows)} 条记录，含重试）' if len(all_rows) != len(rows) else ''
        out(f"运行 {run_id}  {first['trigger']}  session {first['session']}  {'/'.join(versions)}  "
            f"开始 {_beijing(min(row['started_at'] for row in all_rows))}（北京）  {len(rows)} 行{extra}")
        order = {stage: i for i, stage in enumerate(expected or stages_module.STAGE_IDS)}
        table = [[row['stage'], row['status'], str(row['input_count']), str(row['output_count']),
                  _stage_derivation(row), row['reason'] or ''] for row in sorted(rows, key=lambda r: order.get(r['stage'], 99))]
        for line in _table(['阶段', '状态', '输入', '输出', '推导', '原因'], table, indent='  '):
            out(line)
        if expected is None:
            out(f"  阶段版本无法核对：{', '.join(versions)}")
        if missing:
            out(f"  缺少阶段：{', '.join(missing)}")
        for problem in problems:
            out(f'  {problem}')
        if run_id not in reported:
            out(f'  不在 job_reports：{run_id}')
    for report in no_evidence:
        out(f"缺证据  {report['run_id']}  {report['trigger_source']}  {_beijing(report['started_at'])}（北京）")
    passed = ready and not incomplete and not unreported and not no_evidence
    counts = '，'.join(f'{version} {count} 行' for version, count in RUN_STAGE_COUNT.items())
    out(f"汇总：{len(runs)} 个运行，{complete} 个齐全（{counts}），缺阶段行 {incomplete} 个，"
        f"缺证据 {len(no_evidence)} 个，不在 job_reports {unreported} 个 → {'通过' if passed else '不通过'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    """Read-only consistency/stage CLI; exit 1 for mismatches, 2 for an unavailable database."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workflow',
                                     description='计划推导与阶段证据的只读校验。')
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='逐份核对计划与推导是否一致')
    check.add_argument('--since', type=_date_arg, required=True, help='信号日起（含），YYYY-MM-DD')
    check.add_argument('--until', type=_date_arg, help='信号日止（含），YYYY-MM-DD')
    check.add_argument('--kind', choices=PLAN_KINDS, help='只看一种计划')
    stages = commands.add_parser('stages', help='核对一个 session 的阶段证据')
    stages.add_argument('--session', type=_date_arg, required=True, help='交易日，YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        return (_check if args.command == 'check' else _stages)(args, print)
    except Exception as exc:
        if isinstance(exc, core_tx.Unavailable):
            print(f'数据库不可用：{exc}', file=sys.stderr)
            return 2
        raise


if __name__ == '__main__':
    raise SystemExit(main())
