"""Command line for the agent track: checkpoint setup and a manual unit run (§10.3)."""
from __future__ import annotations
import investment_assistant.analysis.agent.runner as analysis_agent_runner

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.analysis.agent')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('setup', help='create the LangGraph checkpoint tables')
    run_parser = sub.add_parser('run', help='run the agent once for one unit')
    run_parser.add_argument('--ticker', required=True)
    run_parser.add_argument('--date', required=True)
    args = parser.parse_args(argv)

    if args.command == 'setup':
        from investment_assistant.core.llm import model
        url = os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL')
        if not url:
            print('INVESTMENT_ASSISTANT_DATABASE_URL 未配置', file=sys.stderr)
            return 1
        model.setup_checkpoints(url)
        print('checkpoint tables ready')
        return 0

    run = analysis_agent_runner.run_agent(args.ticker.upper(), date.fromisoformat(args.date),
                            now=datetime.now(UTC))
    print(json.dumps({'id': str(run['id']), 'status': run['status'], 'reason': run['reason']},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
