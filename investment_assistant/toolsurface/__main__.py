"""python -m investment_assistant.toolsurface — the MCP tool surface over stdio."""
from __future__ import annotations

import argparse
import sys
from datetime import date

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.toolsurface',
                                     description='v3 工具面：一个交易日、一种模式的 MCP 服务（stdio）。')
    parser.add_argument('--session', required=True, help='交易日 YYYY-MM-DD（所有读工具的数据边界）')
    parser.add_argument('--mode', choices=['brief', 'chat'], required=True, help='brief（简报）或 chat（对话）')
    parser.add_argument('--agent-session', required=True, dest='agent_session',
                        help='会话编号，写进每次工具调用记录')
    parser.add_argument('--env-file', help='数据库连接所在的环境文件；不指定则用进程环境里的连接串')
    args = parser.parse_args(argv)
    try:
        session = date.fromisoformat(args.session)
    except ValueError:
        print(f'--session 需为 YYYY-MM-DD：{args.session}', file=sys.stderr)
        return 2
    if args.env_file:
        load_dotenv(args.env_file)                # 连接串只从环境文件或进程环境来，配置文件里没有凭据

    from investment_assistant.config import load_config
    from investment_assistant.toolsurface.env import make_env
    from investment_assistant.toolsurface.jsonrpc import serve

    env = make_env(session=session, mode=args.mode, agent_session=args.agent_session,
                   cfg=load_config())
    serve(env)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
