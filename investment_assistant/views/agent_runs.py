from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from investment_assistant.core import schema as core_schema, tx as core_tx
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.execution import plan_context as execution_plan_context, repo as execution_repo
from investment_assistant.analysis import repo as analysis_repo, synthesis

def agent_runs(*, now: datetime, since: date | None = None) -> dict[str, Any]:
    """Run rows without result payloads, plus coverage: every buy unit in range has a run, is merged into an active
    chain (``coverage.merged``, counted as covered, v2.9 §4.3) or is missing."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        start = since if since is not None else session - timedelta(days=14)
        runs = analysis_repo.list_agent_runs(conn, start)
        total = covered = 0
        missing: list[dict[str, Any]] = []
        merged: list[dict[str, Any]] = []
        merges_ready = core_schema.executable_ready(conn)
        for group in analysis_repo.candidate_groups_since(conn, start):
            ticker, signal_date = group['ticker'], group['signal_date']
            book = execution_plan_context._book(conn, ticker)
            unit = synthesis.synthesize(ticker, signal_date, group['candidates'],
                                        holding=book['position'] is not None,
                                        now=now)
            if unit is None or unit['proposed_action'] != 'buy' or 'conflict' in unit['labels']:
                continue
            total += 1
            if analysis_repo.latest_agent_run(conn, ticker, signal_date) is not None:
                covered += 1
            elif merges_ready and (row := execution_repo.retrigger_for_unit(conn, ticker, signal_date)) is not None:
                covered += 1        # v2.9 §4.3: merged into an active chain, so nothing to propose: not missing
                merged.append({'ticker': ticker, 'signal_date': signal_date,
                               'chain_signal_date': row['chain_signal_date']})
            else:
                missing.append({'ticker': ticker, 'signal_date': signal_date})
    return {'since': start, 'runs': runs,
            'coverage': {'units': total, 'covered': covered, 'merged': len(merged), 'merged_units': merged,
                         'missing': missing}}

def agent_run_detail(run_id: str) -> dict[str, Any]:
    """One run with every tool call, arguments, results and hashes (§10.3 traceability)."""
    with core_tx._tx() as conn:
        run = data_repo.agent_run_by_id(conn, run_id)
        if run is None:
            raise LookupError(f'运行 {run_id} 不存在')
        return {**run, 'id': str(run['id']), 'tool_calls': analysis_repo.agent_tool_calls(conn, run['id'])}
