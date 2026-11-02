"""The aggregate read-only verification CLI (spec D45): ``python -m investment_assistant.workflow check / stages``.

``check --since`` lists the derivation of every plan; ``check --session`` runs :func:`run_checks`, the seven
items of one session -- derivation, holding state, accounts, ``portfolio_checks``, ``proposals_without_changes``,
``data_requests`` and the researcher status -- and prints one ``ok`` / ``mismatch`` / ``skipped`` line each."""
from __future__ import annotations
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.optimize.proposals as optimize_proposals
import investment_assistant.workflow.repo as workflow_repo

import argparse
import sys
import unicodedata
from datetime import UTC, date, datetime, time as dtime, timedelta
from typing import Any, Callable

from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import NY, SH
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.derivation.chain import derive_plan
from investment_assistant.views import derivation as derivation_view
from investment_assistant.core import stages as stages_module
from investment_assistant.core.versions import AGENT_RUN_VERSION
from investment_assistant.workflow import graph as workflow
from investment_assistant.workflow.nodes import accounts as accounts_node
from investment_assistant.workflow.nodes import review as review_node

PLAN_KINDS = ('rule', 'own', 'agent')
RUN_STAGE_COUNT = {'stages-1': 7, 'stages-2': 8, 'stages-3': 9, 'stages-4': 10, 'stages-5': 11, 'stages-6': 13}


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


# ---------------------------------------------------------------- the aggregate (D45)

RESEARCH_STATUSES = frozenset({'ok', 'rejected', 'error', 'disabled', 'carried'})
CHECK_ITEMS = ('derivation', 'holding_state', 'accounts', 'portfolio_checks', 'proposals_without_changes',
               'data_requests', 'research')


def _item(status: str, problems: list[str] | None = None, reason: str | None = None) -> dict[str, Any]:
    return {'status': status, 'problems': list(problems or []), 'reason': reason}


def _verdict(problems: list[str]) -> dict[str, Any]:
    return _item('mismatch' if problems else 'ok', problems)


def _skipped(reason: str) -> dict[str, Any]:
    return _item('skipped', reason=reason)


def _derivation_item(conn, session: date) -> dict[str, Any]:
    """Every ``ok`` plan of the session's units re-derives to what is stored."""
    problems = []
    for plan in execution_repo.plans_since(conn, session):
        if plan['signal_date'] != session or plan['status'] != 'ok':
            continue
        consistency = derive_plan(plan, derivation_view.load_rows(conn, plan))['consistency']
        if consistency['status'] != 'consistent':
            problems.append(f"{plan['ticker']} {str(plan['id'])[:8]}：{consistency['status']}")
    return _verdict(problems)


def _holding_item(conn, session: date) -> dict[str, Any]:
    """Every ticker held at the batch's cutoff has its ``holding_status`` row for the session."""
    if not core_schema.executable_ready(conn):
        return _skipped('migration_029_missing')
    start, _ = review_node._stage_evidence(conn, session)
    cutoff = review_node._run_cutoff(conn, session) or start
    if cutoff is None:
        return _skipped('no_batch')
    return _verdict(review_node._check_holding_status(conn, session, cutoff=cutoff, out=lambda _line: None))


def _accounts_item(conn, session: date, now: datetime) -> dict[str, Any]:
    """Equity (and, for a paper account, the decision) of every account the session's accounts node owed one."""
    if not core_schema.accounts_ready(conn):
        return _skipped('migration_030_missing')
    return _verdict(accounts_node.check_accounts(conn, session=session, now=now))


def _portfolio_item(conn, session: date) -> dict[str, Any]:
    """One ``portfolio_checks`` row per active account for a session whose ``portfolio`` node actually ran (a node
    skipped for its frozen switch, or no daily run at all, owes nothing)."""
    if not core_schema.five_layers_ready(conn):
        return _skipped('migration_031_missing')
    runs = [r for r in workflow_repo.stage_runs_for_session(conn, session)
            if r['job'] == 'daily' and r['stage'] == 'portfolio' and r['status'] != 'skipped']
    if not runs:
        return _skipped('no_portfolio_run')
    ended = max(r['finished_at'] for r in runs)
    problems = []
    for account in execution_repo.list_accounts(conn):
        if not account['opened_on'] or account['opened_on'] > session or account['created_at'] > ended:
            continue
        if execution_repo.portfolio_check_for(conn, account['id'], session) is None:
            problems.append(f"{account['name']}：{session} 没有组合检查行")
    return _verdict(problems)


def _proposals_item(conn) -> dict[str, Any]:
    """No ``proposed`` proposal has a roadmap version or brief behind it yet (spec §8.4: nothing changes before the
    user approves)."""
    if not core_schema.five_layers_ready(conn):
        return _skipped('migration_031_missing')
    return _verdict([f'提案 {proposal_id} 尚未批准却已有版本或实施简报'
                     for proposal_id in optimize_proposals.violations(conn)])


def _data_requests_item(conn, now: datetime) -> dict[str, Any]:
    """No request has stayed ``queued`` for more than a day."""
    if not core_schema.five_layers_ready(conn):
        return _skipped('migration_031_missing')
    return _verdict([f"补查请求 {row['id']} {row['family']} {row['ticker']} 自 {_beijing(row['requested_at'])} 起仍在排队"
                     for row in data_repo.stale_queued_data_requests(conn, before=now - timedelta(days=1))])


def _research_item(conn, session: date) -> dict[str, Any]:
    """Every agent-4 plan run of the session carries a researcher segment with a known status."""
    start, end = session_window(session)
    problems = []
    for run in analysis_repo.unit_runs_with_research(conn, AGENT_RUN_VERSION, start, end):
        segment = run['research']
        status = segment.get('status') if isinstance(segment, dict) else None
        if status not in RESEARCH_STATUSES:
            problems.append(f"{run['ticker']} {run['signal_date']} {run['mode']} {str(run['id'])[:8]}："
                            f"研究员状态 {status if status is not None else '缺失'}")
    return _verdict(problems)


def run_checks(conn, *, session: date, now: datetime) -> dict[str, Any]:
    """Every verification item of one session, read-only: ``{'session', 'ok', 'items': {name: {status, problems,
    reason}}}``. ``status`` is ``ok``, ``mismatch`` or ``skipped`` (the schema or the evidence the item needs is not
    there; ``reason`` says which); ``ok`` is False when any item is a mismatch."""
    items = {
        'derivation': _derivation_item(conn, session),
        'holding_state': _holding_item(conn, session),
        'accounts': _accounts_item(conn, session, now),
        'portfolio_checks': _portfolio_item(conn, session),
        'proposals_without_changes': _proposals_item(conn),
        'data_requests': _data_requests_item(conn, now),
        'research': _research_item(conn, session),
    }
    return {'session': session, 'ok': all(item['status'] != 'mismatch' for item in items.values()), 'items': items}


def _session_check(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    with core_tx.read_only_tx() as conn:
        found = run_checks(conn, session=args.session, now=datetime.now(UTC))
    out(f'核对 {args.session}')
    for name, item in found['items'].items():
        note = f"（{item['reason']}）" if item['reason'] else ''
        out(f"{item['status']:<8}{name}{note}")
        for problem in item['problems']:
            out(f'  {problem}')
    bad = sum(item['status'] == 'mismatch' for item in found['items'].values())
    out(f"汇总：{len(found['items'])} 项，不一致 {bad} 项 → {'通过' if found['ok'] else '不通过'}")
    return 0 if found['ok'] else 1


def main(argv: list[str] | None = None) -> int:
    """Read-only consistency/stage CLI; exit 1 for mismatches, 2 for an unavailable database."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workflow',
                                     description='计划推导与阶段证据的只读校验。')
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='核对计划与推导，或汇总一个交易日的全部核对项')
    check.add_argument('--since', type=_date_arg, help='信号日起（含），YYYY-MM-DD：逐份核对计划与推导')
    check.add_argument('--session', type=_date_arg, help='交易日，YYYY-MM-DD：汇总该日全部核对项（D45）')
    check.add_argument('--until', type=_date_arg, help='信号日止（含），YYYY-MM-DD')
    check.add_argument('--kind', choices=PLAN_KINDS, help='只看一种计划')
    stages = commands.add_parser('stages', help='核对一个 session 的阶段证据')
    stages.add_argument('--session', type=_date_arg, required=True, help='交易日，YYYY-MM-DD')
    args = parser.parse_args(argv)
    if args.command == 'check' and args.since is None and args.session is None:
        parser.error('check 需要 --since 或 --session')
    try:
        if args.command == 'stages':
            return _stages(args, print)
        codes = []
        if args.since is not None:
            codes.append(_check(args, print))
        if args.session is not None:
            codes.append(_session_check(args, print))
        return max(codes)
    except Exception as exc:
        if isinstance(exc, core_tx.Unavailable):
            print(f'数据库不可用：{exc}', file=sys.stderr)
            return 2
        raise


if __name__ == '__main__':
    raise SystemExit(main())
