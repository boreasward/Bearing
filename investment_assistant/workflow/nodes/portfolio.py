"""The ``portfolio`` node (v2.14, spec §7.2, K2): the hard portfolio check of every active account, then the PM's
commentary on the real account.

It runs between ``review`` and ``accounts``. For the real account and each active paper account it reads the same
cutoff-limited plans, positions, pending orders, cash and parameters the account's decision would see, runs the pure
``execution.portfolio.check`` and appends one ``portfolio_checks`` row (the first frozen result of an account and
session wins). Only the real account is then handed to the PM model; its validated commentary is written beside the
hard result, a failed or unavailable model leaves the hard result alone.

K2: with ``portfolio_manager`` off the ENTIRE node is skipped (``skipped / feature_disabled``) — no check, no PM, and
the accounts node says "not checked" instead of consulting any stored row.

A failure of the node itself is the node's own ``failed`` row, never a reason for ``accounts`` to stop: a missing
check does not block (spec §15)."""
from __future__ import annotations

import time as clock_module
from collections import Counter
from typing import Any

import investment_assistant.analysis.agent.runner as analysis_agent_runner
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
from investment_assistant.core import redact as families
from investment_assistant.core import stages as stages_module
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import REAL_ACCOUNT_ID
from investment_assistant.core.tx import Unavailable
from investment_assistant.core.versions import PORTFOLIO_CHECK_VERSION
from investment_assistant.data.calendar import is_trading_day
from investment_assistant.execution import portfolio
from investment_assistant.execution.paper import accounts as execution_paper_accounts
from investment_assistant.workflow.state import _moment, _stage_finish, _stage_skip, _wall, feature_enabled

FLAG = 'portfolio_manager'


def _tickers(inputs: dict) -> set[str]:
    return ({p['ticker'].strip().upper() for p in inputs['plans']}
            | {p['ticker'].strip().upper() for p in inputs['positions']}
            | {o['ticker'].strip().upper() for o in inputs['pending']})


def _check_account(account: dict, *, session, now, run_id: str) -> tuple[dict, dict | None]:
    """The account's stored check, computed and appended when it has none yet (one transaction, the account lock
    held). Returns ``(row, inputs)``; ``inputs`` is None when an earlier run's frozen row was reused."""
    with core_tx._tx() as conn:
        with conn.transaction():
            core_dbutil.lock(conn, f"account:{account['id']}")
            row = execution_repo.portfolio_check_for(conn, account['id'], session)
            if row is not None:
                return row, None
            current = execution_repo.account_by_id(conn, account['id'])
            inputs = execution_paper_accounts.portfolio_inputs(conn, current, session=session, now=now)
            sectors = {ticker: found['group'] for ticker, found in
                       data_repo.latest_sectors(conn, sorted(_tickers(inputs)), cutoff_at=now).items()}
            result = portfolio.check(session=session, plans=inputs['plans'], positions=inputs['positions'],
                                     pending=inputs['pending'], cash=inputs['cash'], equity=inputs['equity'],
                                     params=inputs['params'], sectors=sectors)
            execution_repo.insert_portfolio_check(conn, {
                'account_id': account['id'], 'session': session, 'run_id': run_id, 'cutoff_at': now,
                'version': PORTFOLIO_CHECK_VERSION, 'result': portfolio.to_fact(result)})
            row = execution_repo.portfolio_check_for(conn, account['id'], session)
            assert row is not None
            return row, inputs


def _commentary(account: dict, row: dict, inputs: dict | None, *, session, now, deps, settings, clock, started) -> dict:
    """The real account's PM commentary status: ``{status, reason}`` (``skipped`` when the model is unavailable)."""
    if inputs is None and row['commentary'] is None:     # a reused row: the plans' levels for the PM come fresh
        with core_tx._tx() as conn:
            inputs = execution_paper_accounts.portfolio_inputs(conn, account, session=session, now=now)
    facts = None if inputs is None else {'account': {key: inputs[key] for key in ('cash', 'equity', 'params')},
                                         'plans': inputs['plans'], 'positions': inputs['positions']}
    return analysis_agent_runner.run_pm_commentary(account, row, session=session, now=now, settings=settings,
                                                   deps=deps, clock=clock, started=started, facts=facts)


def run_stage(session, *, now, deps, run_id, acc: stages_module.StageAcc | None, settings, features=None,
              clock=None, started=None) -> dict:
    """The node's work: ``{'accounts': [{account_id, name, ok, blocked, deferred, commentary_status, ...}],
    'cutoff_at'}``. One account's failure is its own ``acc.fail`` line and never stops the others."""
    if not is_trading_day(session):
        return {'accounts': [], 'skipped': 'not_trading_day'}
    if not feature_enabled({'features': features}, FLAG):
        return {'accounts': [], 'skipped': 'feature_disabled'}
    clock = clock or deps.clock or clock_module.monotonic
    started = clock() if started is None else started
    with core_tx._tx() as conn:
        execution_paper_accounts.require_ready(conn)
        if not core_schema.five_layers_ready(conn):
            raise Unavailable('migration_031_missing')
        active = [a for a in execution_repo.list_accounts(conn) if a['opened_on'] and a['opened_on'] <= session]
    results = []
    for account in active:
        item: dict[str, Any] = {'account_id': str(account['id']), 'name': account['name'], 'ok': 0, 'blocked': 0,
                                'deferred': 0, 'commentary_status': None}
        results.append(item)
        try:
            row, inputs = _check_account(account, session=session, now=now, run_id=run_id)
        except Exception as exc:    # noqa: BLE001 - one account's failure never stops the others
            reason = families.sanitize_reason(exc)
            item.update(error=reason)
            if acc is not None:
                acc.fail(account['name'], reason)
            continue
        counts = Counter(v['verdict'] for v in row['result']['verdicts'])
        item.update(ok=counts['ok'], blocked=counts['blocked'], deferred=counts['deferred'])
        if str(account['id']) == str(REAL_ACCOUNT_ID):
            try:
                status = _commentary(account, row, inputs, session=session, now=now, deps=deps, settings=settings,
                                     clock=clock, started=started)
            except Exception as exc:    # noqa: BLE001 - the hard result stands without commentary
                status = {'status': 'error', 'reason': families.sanitize_reason(exc)}
            item.update(commentary_status=status['status'], commentary_reason=status['reason'])
            if acc is not None and status['status'] == 'skipped':
                acc.skip(str(status['reason']), account['name'])
            elif acc is not None and status['status'] in ('rejected', 'error'):
                acc.degrade(f"pm_{status['status']}", status['reason'])
    out = {'accounts': results, 'cutoff_at': now.isoformat()}
    if acc is not None:
        acc.detail['cutoff_at'] = now.isoformat()
        acc.input(len(active))
        acc.output(sum('error' not in item for item in results))
        acc.detail['accounts'] = [{key: value for key, value in item.items() if key != 'error'} for item in results]
    return out


def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The graph node. Switch off → the whole node is skipped; any failure is the node's own ``failed`` row (the
    chain carries on, the accounts node then finds no check and does not block)."""
    _, session = _moment(state)
    summary = state['summary']
    if not feature_enabled(state, FLAG):
        _stage_skip(ctx, 'portfolio', 'feature_disabled')
        return {'summary': summary}
    now = _wall(ctx)()           # upstream plans / reviews were committed after the run cutoff: freeze this node's own
    try:
        summary['portfolio'] = run_stage(session, now=now, deps=ctx.deps, run_id=state.get('run_id'), acc=acc,
                                         settings=ctx.deps.agent_settings, features=state.get('features'),
                                         clock=ctx.clock, started=ctx.clock())
    except Exception as exc:     # noqa: BLE001 - recorded as the node's failure, never raised into the chain
        reason = families.sanitize_reason(exc)
        summary.setdefault('errors', []).append(f'portfolio: {reason}')
        _stage_finish(ctx, 'portfolio', failed=reason or type(exc).__name__)
    return {'summary': summary}
