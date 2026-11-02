"""Account commands and the four ordered daily paper-account phases.

All external work is injected. Account advisory locks cover replay checks and writes;
settlement is deterministic, account-scoped and never changes global plan chains.
"""
from __future__ import annotations
import investment_assistant.analysis.agent.runner as analysis_agent_runner
from investment_assistant.execution.paper import accounts as execution_paper_accounts
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.workflow.repo as workflow_repo

import argparse
import os
import time as clock_module
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5

from investment_assistant.config import load_config
from investment_assistant.core import idempotency as core_idempotency
from investment_assistant.core.constants import ACCOUNTS_AGENT_DEADLINE_S as ACCOUNTS_AGENT_DEADLINE_S
from investment_assistant.data.calendar import add_trading_days, is_trading_day, trading_days_between
from investment_assistant.core import redact as families, tx as service, stages as stages_module
from investment_assistant.core.tx import Unavailable
from investment_assistant.workflow.state import _moment, _wall, _stage_finish, feature_enabled
from typing import Any
from investment_assistant.execution import simulation as plan_shadow
from investment_assistant.execution import accounts
from investment_assistant.execution import ledger
from investment_assistant.execution.paper import fill as paper_fill
from investment_assistant.execution.paper import strategies as paper_strategies
from investment_assistant.data.adapters import bars
from investment_assistant.analysis.agent import graph
from investment_assistant.analysis.agent import tools
from investment_assistant.execution import portfolio_check
from investment_assistant.core.llm import model
from investment_assistant.core import tx as core_tx




def recent_closed_facts(conn,account,*,session,now):
    """Review evidence assembled by the accounts node, outside the execution layer."""
    from investment_assistant.review import outcomes as review
    from investment_assistant.review import tracks

    trades,splits,books=execution_paper_accounts.account_books(conn,account['id'],session=session,now=now)
    recent=[]
    spy=data_repo.read_bars(conn,'SPY',session,cutoff_at=now,limit=400)
    for ticker,book in books.items():
        prices=data_repo.read_bars(conn,ticker,session,cutoff_at=now,limit=400)
        for seg in book['closed']:
            if seg['closed_at'].astimezone(accounts.NY).date()<add_trading_days(session,-(portfolio_check.PORTFOLIO_RECENT_SESSIONS-1)): continue
            buy=next(t for t in trades if t['id']==seg['buy_trade_id'])
            sale=next(t for t in trades if t['id']==seg['sell_trade_id'])
            frozen=execution_repo.plan_by_id(conn,buy['plan_id']) if buy.get('plan_id') else None
            actual=tracks.actual_track_v2(plans_by_trade={buy['id']:frozen} if frozen else {},
                buys=[buy],segments=[seg],open_lots=[],last_close=None,splits=splits[ticker],session=session,unit_valid_until=None)
            recent.append({'ticker':ticker,**review.trade_segment_review(seg,prices,spy),'r':actual['r'],
                           'exit_reason':(sale.get('detail') or {}).get('exit_reason')})
    return recent


def run_stage(session,*,now,deps,run_id,acc,settings,features=None):
    if not is_trading_day(session): return {'accounts':[],'skipped':'not_trading_day'}
    clock=deps.clock or clock_module.monotonic; started=clock()
    with core_tx._tx() as conn:
        execution_paper_accounts.require_ready(conn)
        active=[a for a in execution_repo.list_accounts(conn) if a['opened_on'] and a['opened_on']<=session]
    papers=[a for a in active if a['kind']=='paper']; results={str(a['id']):{'account_id':str(a['id']),'name':a['name'],
        'settled':dict(filled=0,voided=0,expired=0,cancelled=0,exits=0),'equity':None,'holdings':[],'decision':None} for a in active}
    failed=set()
    def phase(account,fn,**kw):
        aid=str(account['id'])
        try:
            with core_tx._tx() as conn:
                with conn.transaction(): return fn(conn,account,**kw)
        except Exception as exc:
            results[aid]['error']=families.sanitize_reason(exc); failed.add(aid)
            if acc: acc.fail(account['name'], families.sanitize_reason(exc))
    # Settlement of every paper account precedes real equity, statuses, then decisions.
    for account in papers:
        out=phase(account,execution_paper_accounts.settle_account,through=session,now=now,run_id=run_id)
        if out:
            results[str(account['id'])].update(out)
            if out.get('equity') and out['equity']['cash']<0:
                # Settlement and protective exits stay valid; cash validation prevents new buys.
                results[str(account['id'])]['error']='negative_cash'
                if acc: acc.fail(account['name'],'negative_cash')
    try:
        with core_tx._tx() as conn: real=execution_paper_accounts.write_real_equity(conn,session=session,now=now,run_id=run_id)
        if str(execution_paper_accounts.REAL_ACCOUNT_ID) in results: results[str(execution_paper_accounts.REAL_ACCOUNT_ID)]['equity']=real
    except Exception as exc:
        failed.add(str(execution_paper_accounts.REAL_ACCOUNT_ID))
        if str(execution_paper_accounts.REAL_ACCOUNT_ID) in results: results[str(execution_paper_accounts.REAL_ACCOUNT_ID)]['error']=families.sanitize_reason(exc)
        if acc: acc.fail('真实账户', families.sanitize_reason(exc))
    for account in papers:
        if str(account['id']) not in failed:
            held=phase(account,execution_paper_accounts.write_paper_holdings,session=session,now=now,run_id=run_id)
            results[str(account['id'])]['holdings']=held or []
    host_cancellations={}
    for account in papers:
        aid=str(account['id'])
        if aid not in failed:
            changed=phase(account,execution_paper_accounts.reconcile_pending_buys,session=session,now=now)
            host_cancellations[aid]=changed or []
            results[aid]['settled']['cancelled']+=len(changed or [])
    unavailable=None
    # K2: the Agent-managed decision uses the stored portfolio check only when this run's frozen switch is on.
    checking=feature_enabled({'features':features},'portfolio_manager') if any(a['strategy']=='agent_managed' for a in papers) else False
    def decide_agent(conn,account,**kw):
        state=execution_paper_accounts.portfolio_check_state(conn,account,session=session,enabled=checking)
        return analysis_agent_runner.run_portfolio_decision(conn,account,portfolio_check=state,**kw)
    for strategy in ('follow_plans','agent_managed'):
        for account in papers:
            if account['strategy']!=strategy or str(account['id']) in failed: continue
            kw={'session':session,'now':now,'run_id':run_id,'host_cancellations':host_cancellations.get(str(account['id']),[])}
            if strategy=='agent_managed' and unavailable:
                row=phase(account,execution_paper_accounts._skip_decision,reason=unavailable,**kw)
            else:
                if strategy=='agent_managed': kw.update(settings=settings,deps=deps,clock=clock,started=started,recent_closed=recent_closed_facts)
                else: kw.update(recent_closed=recent_closed_facts)
                row=phase(account,execution_paper_accounts.decide_follow_plans if strategy=='follow_plans' else decide_agent,**kw)
            if strategy=='agent_managed' and row and row.get('status_reason') in ('quota_exhausted','not_connected','model_unavailable'):
                unavailable=row['status_reason']
            if row: results[str(account['id'])]['decision']={'status':row['status'],'orders_placed':0,'orders_rejected':0,
                'skipped':[],**(row.get('detail') or {}),'status_reason':row.get('status_reason')}
    out={'accounts':list(results.values()),'cutoff_at':now.isoformat()}
    if acc:
        acc.input(len(active)); acc.output(len(active)-len(failed)); acc.detail.update(service._jsonable(out))
    return out


def check_accounts(conn, *, session, now):
    """Only sessions with an actually executed accounts node require a daily decision.

    Offline settlement intentionally creates intermediate equity rows without invented decisions.
    Accounts created after that run do not retrospectively owe it a decision.
    """
    if not core_schema.accounts_ready(conn):
        return ['migration_030_missing']
    runs=[r for r in workflow_repo.stage_runs_for_session(conn,session)
          if r['job']=='daily' and r['stage']=='accounts' and r['status']!='skipped']
    if not runs: return []
    ended=max(r['finished_at'] for r in runs)
    problems=[]
    for account in execution_repo.list_accounts(conn):
        if not account['opened_on'] or account['opened_on']>session or account['created_at']>ended: continue
        rows=execution_repo.equity_days(conn,account['id'],from_session=session,to_session=session)
        if not rows: problems.append(f"{account['name']}：{session} missing equity")
        if account['kind']=='paper' and execution_repo.decision_for(conn,account['id'],session) is None:
            problems.append(f"{account['name']}：{session} missing decision")
    return problems


def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """Account failures are recorded without preventing push or retro; its own 600-second budget starts here."""
    _, session = _moment(state)
    # Upstream plans/reviews are committed after the run cutoff. Freeze this node's own cutoff once.
    now = _wall(ctx)()
    if acc is not None:
        acc.detail['cutoff_at'] = now.isoformat()
    summary = state['summary']
    try:
        summary['accounts'] = run_stage(session, now=now, deps=ctx.deps,
            run_id=state.get('run_id'), acc=acc, settings=ctx.deps.agent_settings, features=state.get('features'))
    except Exception as exc:
        reason = families.sanitize_reason(exc)
        summary.setdefault('errors', []).append(f'accounts: {reason}')
        _stage_finish(ctx, 'accounts', failed=reason or type(exc).__name__)
    return {'summary': summary}
