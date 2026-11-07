"""python -m investment_assistant.daily — run the day or manage the runtime settings.

Exit codes (run): 0 成功；1 事实任务错误；2 参数错误（或数据库不可用）；3 已有运行时会话。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from investment_assistant.runtime.base import RuntimeSpec

# 状态 -> 退出码；draft 是「未选定模型」的正常产物（spec §5），退出码 0
EXIT_CODES = {"ok": 0, "final": 0, "draft": 0, "error": 1, "busy": 3}


def _load_env(env_file: str | None) -> None:
    from dotenv import load_dotenv

    load_dotenv(env_file) if env_file else load_dotenv()


def _spec(args) -> "RuntimeSpec | None":
    """The per-run override: all three keys or none (v3 设计 §15.4 单次运行临时指定)."""
    given = [name for name, value in (("runtime", args.runtime), ("provider", args.provider), ("model", args.model)) if value]
    if len(given) == 3:
        from investment_assistant.runtime.base import RuntimeSpec

        return RuntimeSpec(runtime=args.runtime, provider=args.provider, model=args.model)
    if given:
        raise ValueError(f"临时指定运行时需要 --runtime、--provider、--model 同时给出，缺：{given}")
    return None


def _run(args) -> int:
    from pathlib import Path

    _load_env(args.env_file)
    from investment_assistant.core.tx import Unavailable
    from investment_assistant.daily.job import run_daily

    try:
        result = run_daily(
            collect=not args.no_collect,
            spec=_spec(args),
            env_file=Path(args.env_file) if args.env_file else None,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Unavailable as exc:
        print(f"数据库不可用：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_CODES.get(result["status"], 0)


def _settings(args) -> int:
    _load_env(None)
    import dataclasses

    import investment_assistant.core.tx as core_tx
    from investment_assistant.core.tx import Unavailable
    from investment_assistant.runtime import settings as runtime_settings
    from investment_assistant.runtime.base import RuntimeSpec
    from investment_assistant.runtime.settings import default_pi_dir

    try:
        if args.settings_command == "show":
            with core_tx.read_only_tx() as conn:
                spec = runtime_settings.current(conn)
            print(json.dumps(dataclasses.asdict(spec) if spec else None, ensure_ascii=False))
            return 0
        if args.settings_command == "options":
            print(json.dumps(runtime_settings.options(default_pi_dir()), ensure_ascii=False))
            return 0
        if not (args.runtime and args.provider and args.model):
            print("settings set 需要 --runtime、--provider、--model 全部给出", file=sys.stderr)
            return 2
        spec = RuntimeSpec(runtime=args.runtime, provider=args.provider, model=args.model)
        try:
            with core_tx._tx() as conn:
                runtime_settings.save(conn, spec, pi_dir=default_pi_dir())
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print("已保存。")
        return 0
    except Unavailable as exc:
        print(f"数据库不可用：{exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m investment_assistant.daily",
        description="每日任务：采集与事实计算、计划复查、简报会话与推送；运行时设置的管理。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="跑一次每日任务（默认先采集）")
    run.add_argument("--no-collect", action="store_true", help="跳过采集，直接用库里已有的数据")
    run.add_argument("--runtime", choices=["pi", "fake"], help="临时指定运行时（需与 --provider/--model 同给）")
    run.add_argument("--provider", help="临时指定提供方")
    run.add_argument("--model", help="临时指定模型")
    run.add_argument("--env-file", help="数据库连接所在的环境文件（默认加载仓库 .env）")
    conf = commands.add_parser("settings", help="查看或修改运行时设置")
    settings_commands = conf.add_subparsers(dest="settings_command", required=True)
    settings_commands.add_parser("show", help="打印当前设置")
    settings_commands.add_parser("options", help="列出可选的运行时、提供方与模型")
    set_parser = settings_commands.add_parser("set", help="保存设置（保存前检查可用性）")
    set_parser.add_argument("--runtime", choices=["pi", "fake"], required=True)
    set_parser.add_argument("--provider", required=True)
    set_parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    return _run(args) if args.command == "run" else _settings(args)


if __name__ == "__main__":
    raise SystemExit(main())
