"""python -m investment_assistant.facts — the v3 facts job CLI: run the day, show a draft."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from dotenv import load_dotenv

from investment_assistant.core.tx import Unavailable


def _show(args) -> int:
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import repo as facts_repo

    session = date.fromisoformat(args.session) if args.session else None
    try:
        with core_tx.read_only_tx() as conn:
            run = facts_repo.latest_run(conn, session)
    except Unavailable as exc:
        print(f'数据库不可用：{exc}', file=sys.stderr)
        return 2
    if run is None:
        print('还没有运行记录。')
        return 1
    print(run['draft_md'], end='')
    return 0


def _run(args) -> int:
    from investment_assistant.facts.job import run_facts

    try:
        result = run_facts(collect=not args.no_collect, trigger=args.trigger)
    except Unavailable as exc:
        print(f'数据库不可用：{exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ok' else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.facts',
                                     description='v3 事实任务：跑一天的采集与规则名单，或查看草稿。')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='跑一次事实任务（默认先采集再计算）')
    run.add_argument('--no-collect', action='store_true', help='跳过采集，直接用库里已有的数据')
    run.add_argument('--trigger', choices=['scheduled', 'manual_batch'], default='manual_batch',
                     help='写进运行记录的触发来源')
    show = commands.add_parser('show', help='打印某交易日最新一条运行的草稿')
    show.add_argument('--session', help='交易日 YYYY-MM-DD，缺省为最新一条运行')
    args = parser.parse_args(argv)
    load_dotenv()
    return _run(args) if args.command == 'run' else _show(args)


if __name__ == '__main__':
    raise SystemExit(main())
