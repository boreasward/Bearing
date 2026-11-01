"""Account commands and the four ordered daily paper-account phases.

All external work is injected. Account advisory locks cover replay checks and writes;
settlement is deterministic, account-scoped and never changes global plan chains.
"""
from __future__ import annotations

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
from investment_assistant.workbench import (accounts, bars, families, ledger, paper_fill, paper_strategies,
                                               plan_shadow, repository, service)
from investment_assistant.workbench.agent import graph, model, portfolio_check, tools
from investment_assistant.core import tx as core_tx

REAL_ACCOUNT_ID = accounts.REAL_ACCOUNT_ID
REAL_EQUITY_RECOMPUTE_SESSIONS = 5
ZERO = Decimal(0)


def require_ready(conn):
    if not repository.accounts_ready(conn):
        raise service.Unavailable('migration_030_missing')


def require_account(conn, account_id):
    require_ready(conn)
    aid = ledger._uuid(account_id, 'account_id')
    row = repository.account_by_id(conn, aid)
    if row is None:
        raise ledger.NotFound('account not found')
    return row


def effective_params(conn, account, session=None):
    """Public defaults plus effective values; audit metadata never reaches a form or model."""
    params = {**accounts.default_params(load_config().workbench),
              **{k: v for k, v in account['params'].items() if not k.startswith('_')}}
    if session is not None:
        for change in reversed(repository.param_changes(conn, account['id'])):
            if change['kind'] == 'params' and change['effective_from'] > session:
                params = {**accounts.default_params(load_config().workbench),
                          **{k:v for k,v in (change['params_before'] or {}).items() if not k.startswith('_')}}
    return accounts.validate_params(params)


def _writable(account):
    if account['status'] != 'active':
        raise ledger.Conflict('account_closed')


def _audit_replay(conn, aid, request_id, digest):
    for change in repository.param_changes(conn, aid):
        meta = (change.get('params_after') or {}).get('_request', {})
        if meta.get('id') == str(request_id):
            if meta.get('hash') != digest:
                raise ledger.Conflict('request_id was already used with different content')
            return change
    return None


def create_paper_account(payload, *, now):
    original = dict(payload)
    supplied = {**payload, 'opened_on': payload.get('opened_on') or add_trading_days(now.astimezone(accounts.NY).date(), 1)}
    rec = accounts.validate_new_account(supplied, defaults=accounts.default_params(load_config().workbench))
    digest = ledger.request_hash({'name':rec['name'], 'strategy':rec['strategy'], 'opening_cash':rec['opening_cash'],
        'opened_on': original.get('opened_on'), 'params':accounts.validate_params(original.get('params', {}))})
    with core_tx._tx() as conn:
        require_ready(conn)
        repository.lock(conn, 'account:create')
        existing = repository.account_by_id(conn, rec['request_id'])
        if existing:
            if existing['kind'] != 'paper' or not _audit_replay(conn, existing['id'], rec['request_id'], digest):
                raise ledger.Conflict('request_id was already used with different content')
            return {'status':'existing','account':_result_account(conn,existing,now=now)}
        if repository.account_by_name(conn, rec['name']):
            raise ledger.Conflict('account_name_exists')
        row = repository.insert_account(conn, {**rec,'id':rec['request_id']})
        repository.insert_param_change(conn, {'account_id':row['id'],'kind':'opening','effective_from':rec['opened_on'],
            'params_after': {'opening_cash':rec['opening_cash'],'opened_on':rec['opened_on'],'params':rec['params'],
                             '_request':{'id':str(rec['request_id']),'hash':digest}},'reason':'创建模拟账户'})
        return {'status':'created','account':_result_account(conn,row,now=now)}


def _public(conn, row):
    return {**row, 'params':effective_params(conn,row), 'strategy_text':accounts.STRATEGY_TEXT[row['strategy']],
            'opened':row['opened_on'] is not None}


def _result_account(conn, row, *, now):
    from investment_assistant.workbench.account_views import account_row
    return account_row(conn, row, session=bars.latest_closed_session(now), now=now)


def open_real_account(account_id, payload, *, now):
    rec=accounts.validate_opening(payload)
    request_id=ledger._uuid(payload.get('request_id'),'request_id')
    if rec['opened_on'] > now.astimezone(accounts.NY).date():
        raise ledger.LedgerError('opened_on cannot be in the future')
    with core_tx._tx() as conn:
        row=require_account(conn,account_id); repository.lock(conn,f"account:{row['id']}")
        row=require_account(conn,row['id'])
        if row['kind']!='real': raise ledger.LedgerError('paper_account_is_host_managed')
        if row['opened_on'] is not None: raise ledger.Conflict('already_opened')
        # Opening is a current write invariant. A record committed while this
        # request waited for the account lock must be included despite its later recorded_at.
        trades=repository.list_trades(conn,account_id=row['id'])
        flows=repository.list_flows(conn,account_id=row['id'])
        earliest=[t['executed_at'].astimezone(accounts.NY).date() for t in trades if t['voided_at'] is None]
        earliest += [f['flow_date'] for f in flows if f['voided_at'] is None]
        if earliest and rec['opened_on']>min(earliest): raise ledger.LedgerError('opened_on_after_records')
        row=repository.set_account_opening(conn,row['id'],opening_cash=rec['opening_cash'],opened_on=rec['opened_on'])
        repository.insert_param_change(conn, {'account_id':row['id'],'kind':'opening','params_after':{
            **rec,'_request':{'id':str(request_id),'hash':ledger.request_hash(rec)}},
            'effective_from':rec['opened_on'],'reason':'真实账户开账'})
        written=[]
        for p in rec['opening_positions']:
            fill={'account_id':row['id'],'request_id':uuid5(request_id,p['ticker']),'ticker':p['ticker'],
                'side':'buy','quantity':p['quantity'],'price':p['cost_per_share'],'fee':ZERO,
                'executed_at':datetime.combine(rec['opened_on'],time(16),accounts.NY),'note':None,
                'plan_id':None,'own_plan':None,'recorded_by':'user','detail':{'opening_position':True}}
            written.append(repository.insert_trade(conn,fill,ledger.trade_hash(fill),recorded_at=now))
        return {'account':_result_account(conn,row,now=now),'trades':written}


def change_params(account_id,payload,*,now):
    request_id=ledger._uuid(payload.get('request_id'),'request_id')
    params=accounts.validate_params(payload.get('params'))
    reason=ledger._text(payload.get('reason'),'reason')
    digest=ledger.request_hash({'params':params,'reason':reason})
    with core_tx._tx() as conn:
        row=require_account(conn,account_id); repository.lock(conn,f"account:{row['id']}")
        row=require_account(conn,row['id']); _writable(row)
        replay=_audit_replay(conn,row['id'],request_id,digest)
        if replay: return {'account':_result_account(conn,row,now=now),'change':_clean_change(replay)}
        before=effective_params(conn,row); after={**before,**params}
        change=repository.insert_param_change(conn, {'account_id':row['id'],'kind':'params','params_before':before,
            'params_after':{**after,'_request':{'id':str(request_id),'hash':digest}},'reason':reason,
            'effective_from':add_trading_days(now.astimezone(accounts.NY).date(),1)})
        row=repository.update_account_params(conn,row['id'],after)
        return {'account':_result_account(conn,row,now=now),'change':_clean_change(change)}


def _clean_change(change):
    return {**change,**{key:{k:v for k,v in (change[key] or {}).items() if not k.startswith('_')}
                       for key in ('params_before','params_after')}}


def close(account_id,payload,*,now):
    reason=ledger._text(payload.get('reason'),'reason')
    with core_tx._tx() as conn:
        row=require_account(conn,account_id); repository.lock(conn,f"account:{row['id']}")
        row=require_account(conn,row['id'])
        if row['kind']=='real': raise ledger.LedgerError('cannot_close_real_account')
        closed=repository.close_account(conn,row['id'],reason,closed_at=now)
        # A closed account is never settled again: release its resting orders in the same transaction.
        for order in repository.pending_orders(conn,row['id']):
            repository.settle_order(conn,order['id'],status='cancelled',status_reason='account_closed',
                settled_on=now.astimezone(accounts.NY).date())
        return _result_account(conn,closed or row,now=now)


def known_flows(conn,aid,now):
    return [{**f,'voided_at':f['voided_at'] if f['voided_at'] and f['voided_at']<=now else None}
            for f in repository.list_flows(conn,account_id=aid) if f['recorded_at']<=now]


def account_books(conn,aid,*,session,now):
    trades=[t for t in repository.list_trades(conn,account_id=aid,cutoff=now)
            if t['executed_at'].astimezone(accounts.NY).date()<=session]
    splits={t['ticker']:repository.split_events(conn,t['ticker'],cutoff_at=now) for t in trades}
    return trades,splits,accounts.book(trades,splits_by_ticker=splits)


def _equity(conn,account,session,now,run_id,*,extra=None):
    trades,splits,books=account_books(conn,account['id'],session=session,now=now)
    closes={}
    for ticker in books:
        prices=repository.read_bars(conn,ticker,session,cutoff_at=now,limit=1)
        if prices: closes[ticker]=(prices[-1]['price_date'],prices[-1]['close'])
    previous=repository.latest_equity_day(conn,account['id'],on_or_before=add_trading_days(session,-1))
    values=accounts.equity_day(account,session=session,trades=trades,flows=known_flows(conn,account['id'],now),
        closes=closes,splits_by_ticker=splits,previous=previous,run_id=run_id or 'accounts')
    # Compare the same precision PostgreSQL persists, so fractional lots do not manufacture revisions.
    for key in ('cash','positions_value','equity','realized_pnl_cum','unrealized_pnl','net_flows_cum','day_pnl'):
        if values[key] is not None:
            values[key]=Decimal(str(values[key])).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)
            if values[key]==0:
                values[key]=Decimal('0.000000')
    if extra: values['detail'].update(extra)
    current=repository.latest_equity_day(conn,account['id'],on_or_before=session)
    keys=('cash','positions_value','equity','realized_pnl_cum','unrealized_pnl','net_flows_cum','day_pnl',
          'positions_count','detail','account_version')
    if current and current['session']==session and all(core_idempotency._plain(current[k])==core_idempotency._plain(values[k]) for k in keys):
        return current
    return repository.insert_equity_day(conn,{**values,'computed_at':now})


def write_real_equity(conn,*,session,now,run_id):
    account=repository.account_by_id(conn,REAL_ACCOUNT_ID)
    if not account or account['status']!='active' or not account['opened_on'] or account['opened_on']>session: return None
    repository.lock(conn,f'account:{REAL_ACCOUNT_ID}')
    latest=repository.latest_equity_day(conn,REAL_ACCOUNT_ID,on_or_before=session)
    start=add_trading_days(session,-(REAL_EQUITY_RECOMPUTE_SESSIONS-1))
    start=min(start,add_trading_days(latest['session'],1)) if latest else account['opened_on']
    latest=None
    for day in trading_days_between(max(account['opened_on'],start),session):
        latest=_equity(conn,account,day,now,run_id)
    return latest


def _persisted_holdings(conn,account,session,now):
    trades,splits,books=account_books(conn,account['id'],session=session,now=now)
    previous=repository.latest_equity_day(conn,account['id'],on_or_before=session)
    saved=(previous or {}).get('detail',{}).get('paper_states',{})
    held=[]; plans={}
    by_id={t['id']:t for t in trades}
    for ticker,book in books.items():
        if not book['position']: continue
        first=by_id[book['open'][0]['buy_trade_id']]
        plan=repository.plan_by_id(conn,first['plan_id']) if first.get('plan_id') else None
        if plan is None: raise ValueError('paper position has no frozen plan')
        plans[str(plan['id'])]=plan
        factor=plan_shadow._basis_factor(plan['signal_date'],splits[ticker])
        prior=saved.get(ticker)
        if prior:
            state=paper_fill._state(prior['state'])
            ratio=factor/Decimal(str(prior.get('split_factor',1)))
            if ratio != 1:
                state=replace(state, **{key:getattr(state,key)/ratio for key in paper_fill._DECIMAL_FIELDS
                                        if getattr(state,key) is not None})
        else:
            raise ValueError('paper position state missing')
        held.append({'ticker':ticker,**book['position'],'plan_id':plan['id'],
                     'chain_signal_date':prior.get('chain_signal_date',plan['signal_date']),
                     'state':state,'split_factor':factor})
    return held,plans,splits


def settle_account(conn,account,*,through,now,run_id):
    repository.lock(conn,f"account:{account['id']}")
    account=repository.account_by_id(conn,account['id'])
    counts=dict(filled=0,voided=0,expired=0,cancelled=0,exits=0)
    out={'settled':counts,'equity':None,'holdings':[]}
    if account['status']!='active' or not account['opened_on'] or account['opened_on']>through: return out
    start=add_trading_days(account['settled_through'],1) if account['settled_through'] else account['opened_on']
    for session in trading_days_between(start,through):
        active={**account,'params':effective_params(conn,account,session)}
        holdings,plans,splits=_persisted_holdings(conn,active,session,now)
        pending=repository.pending_orders(conn,account['id'])
        for order in pending:
            if order.get('plan_id') and str(order['plan_id']) not in plans:
                plans[str(order['plan_id'])]=repository.plan_by_id(conn,order['plan_id'])
        tickers={h['ticker'] for h in holdings}|{o['ticker'] for o in pending}
        prices={}
        for ticker in tickers:
            splits[ticker]=repository.split_events(conn,ticker,cutoff_at=now)
            rows=repository.read_bars(conn,ticker,session,cutoff_at=now,limit=1)
            if rows and rows[-1]['price_date']==session: prices[ticker]=rows[-1]
        result=paper_fill.settle_day(session=session,account=active,bars_by_ticker=prices,pending_orders=pending,
            holdings=holdings,plans_by_id=plans,splits_by_ticker=splits)
        trades_by_order={}
        for index,fill in enumerate(result['fills']):
            request=uuid5(NAMESPACE_URL,f"paper-fill:{account['id']}:{session}:{index}:{fill['ticker']}:{fill['side']}:{fill['plan_id']}")
            record={**fill,'request_id':request,'note':None,'own_plan':None}
            stored=repository.trade_by_request(conn,request,account_id=account['id'])
            if stored is None: stored=repository.insert_trade(conn,record,ledger.trade_hash(record),recorded_at=now)
            oid=(fill.get('detail') or {}).get('order_id')
            if oid: trades_by_order[str(oid)]=stored['id']
            if fill['side']=='sell': counts['exits']+=1
        for update in result['order_updates']:
            repository.settle_order(conn,update['order_id'],status=update['status'],status_reason=update['status_reason'],
                settled_on=session,trade_id=trades_by_order.get(str(update['order_id'])))
            if update['status'] in counts: counts[update['status']]+=1
        for done in result['chain_done']:
            day=done['chain_signal_date']; day=date.fromisoformat(day) if isinstance(day,str) else day
            repository.insert_chain_done(conn,account_id=account['id'],**{**done,'chain_signal_date':day})
        metadata={h['ticker']:h for h in holdings}
        for order in pending:
            if order['action']=='buy' and str(order['id']) in trades_by_order: metadata[order['ticker']]=order
        states={}
        for ticker,state in result['states'].items():
            info=metadata[ticker]; plan=plans[str(info['plan_id'])]
            states[ticker]={'state':asdict(state),'plan_id':info['plan_id'],
                'chain_signal_date':info.get('chain_signal_date',plan['signal_date']),
                'split_factor':plan_shadow._basis_factor(plan['signal_date'],splits.get(ticker,[]))}
        out['equity']=_equity(conn,account,session,now,run_id,extra={'paper_states':states,'settlement_gaps':result['bars_missing']})
        repository.advance_settled_through(conn,account['id'],session)
    if out['equity'] is None:
        out['equity']=repository.latest_equity_day(conn,account['id'],on_or_before=through)
    return out


def write_paper_holdings(conn,account,*,session,now,run_id):
    from investment_assistant.workbench import plan_review
    repository.lock(conn,f"account:{account['id']}")
    account=repository.account_by_id(conn,account['id'])
    if account['status']!='active' or account['opened_on']>session: return []
    rows=repository.holding_status_for(conn,account_id=account['id'],session=session)
    already={r['ticker'] for r in rows}
    _,_,books=account_books(conn,account['id'],session=session,now=now)
    for ticker,book in books.items():
        if not book['position'] or ticker in already: continue
        row=plan_review._holding_row(conn,ticker,session,run_id,now=now,skipped={},unavailable=None,account_id=account['id'])
        if row: rows.append(repository.insert_holding_status(conn,row))
    return rows


def _buyable_at(conn, *, session, now):
    """The shared, cutoff-limited chain view for decisions and mandatory host cancels."""
    from investment_assistant.workbench import plan_review
    chain_items=[]
    for ticker in sorted({c['ticker'] for c in repository.all_chains(conn)}):
        for item in plan_review.ticker_chain_items(conn,ticker,session=session,now=now,point_in_time=True):
            item['plans_by_id']={str(v['plan_id']):repository.plan_by_id(conn,v['plan_id']) for v in item['chain']['versions']}
            chain_items.append(item)
    return chain_items,paper_strategies.buyable(chain_items,next_session=add_trading_days(session,1))


def reconcile_pending_buys(conn, account, *, session, now):
    """After settlement, cancel obsolete pending buys regardless of strategy/model availability.

    Filled orders and frozen holdings are untouched. A stored daily decision makes
    the whole same-session attempt a replay, including this host-only step.
    """
    repository.lock(conn,f"account:{account['id']}")
    account=repository.account_by_id(conn,account['id'])
    if account['status']!='active' or not account['opened_on'] or account['opened_on']>session:
        return []
    if repository.decision_for(conn,account['id'],session):
        return []
    pending=[o for o in repository.pending_orders(conn,account['id'])
             if o['action']=='buy' and o['placed_on']<=session]
    if not pending:
        return []
    _,items=_buyable_at(conn,session=session,now=now)
    cancellations=paper_strategies.follow_plans_orders(items,pending_orders=pending,session=session)['cancel']
    changed=[]
    for intent in cancellations:
        updated=repository.settle_order(conn,intent['order_id'],status='cancelled',
            status_reason=intent['reason'],settled_on=session)
        if updated is not None:
            changed.append(intent)
    return changed


def decision_facts(conn,account,*,session,now):
    from investment_assistant.workbench import plan_review, review
    aid=account['id']; params=effective_params(conn,account,session)
    trades,splits,books=account_books(conn,aid,session=session,now=now)
    equity=repository.latest_equity_day(conn,aid,on_or_before=session)
    positions=[]; held=set(); recent=[]
    spy=repository.read_bars(conn,'SPY',session,cutoff_at=now,limit=400)
    for ticker,book in books.items():
        prices=repository.read_bars(conn,ticker,session,cutoff_at=now,limit=400)
        p=book['position']
        if p:
            lot=next(t for t in trades if t['id']==book['open'][0]['buy_trade_id'])
            version=repository.version_by_plan(conn,lot['plan_id']) if lot.get('plan_id') else None
            held.add((ticker,version['signal_date'] if version else lot['executed_at'].date()))
            positions.append({'ticker':ticker,**p,'market_value':p['quantity']*Decimal(str(prices[-1]['close'])) if prices else ZERO})
        for seg in book['closed']:
            if seg['closed_at'].astimezone(accounts.NY).date()<add_trading_days(session,-(portfolio_check.PORTFOLIO_RECENT_SESSIONS-1)): continue
            buy=next(t for t in trades if t['id']==seg['buy_trade_id'])
            sale=next(t for t in trades if t['id']==seg['sell_trade_id'])
            frozen=repository.plan_by_id(conn,buy['plan_id']) if buy.get('plan_id') else None
            actual=service._tracks_module.actual_track_v2(plans_by_trade={buy['id']:frozen} if frozen else {},
                buys=[buy],segments=[seg],open_lots=[],last_close=None,splits=splits[ticker],session=session,unit_valid_until=None)
            recent.append({'ticker':ticker,**review.trade_segment_review(seg,prices,spy),'r':actual['r'],
                           'exit_reason':(sale.get('detail') or {}).get('exit_reason')})
    orders=[o for o in repository.orders_for(conn,aid,limit=100000) if o['placed_on']<=session]
    pending=[o for o in orders if o['status']=='pending']
    for order in pending:
        if order['action']=='buy':
            p=repository.plan_by_id(conn,order['plan_id'])
            ss=repository.split_events(conn,order['ticker'],cutoff_at=now)
            version=repository.version_by_plan(conn,p['id'])
            factor=plan_shadow._basis_factor(p['signal_date'],[(d,f) for d,f in ss if d<=order['placed_on']])
            order.update(entry_low=p['entry_low']/factor,entry_high=p['entry_high']/factor,valid_until=p['valid_until'],
                         version_no=version['version_no'] if version else None)
            order['reserved_cash']=Decimal(str(order['shares']))*p['entry_high']/plan_shadow._basis_factor(p['signal_date'],[(d,f) for d,f in ss if d<=order['placed_on']])+Decimal(str(params['fee_per_trade']))
    chain_items,candidates=_buyable_at(conn,session=session,now=now)
    candidates=paper_strategies.account_filter(candidates,held_chains=held,done=repository.chain_done(conn,aid),
        tried_plan_ids=repository.tried_plan_ids(conn,aid),pending_chains={(o['ticker'],o['chain_signal_date']) for o in pending if o['action']=='buy'})
    cash=accounts.cash(account,trades,known_flows(conn,aid,now),cutoff=session)
    for item in candidates:
        factor=plan_shadow._basis_factor(item['signal_date'],repository.split_events(conn,item['ticker'],cutoff_at=now))
        for key in ('entry_low','entry_high','ref_price','stop','target1','atr'):
            if item.get(key) is not None: item[key]=Decimal(str(item[key]))/factor
    _size_candidates(candidates,positions,pending,cash,params)
    for position in positions:
        ticker=position['ticker']
        linked=next(t for t in trades if t['id']==books[ticker]['open'][0]['buy_trade_id'])
        frozen=repository.plan_by_id(conn,linked['plan_id']) if linked.get('plan_id') else None
        version=repository.version_by_plan(conn,linked['plan_id']) if linked.get('plan_id') else None
        states=repository.holding_status_for(conn,account_id=aid,ticker=ticker)
        state=next((r for r in reversed(states) if r['session']<=session),None)
        if state is None:
            state=plan_review._holding_row(conn,ticker,session,None,now=now,skipped={},unavailable=None,account_id=aid) or {}
        fields=('last_close','r_now','stop','stop_mode','target1','t1_reached','held_sessions','max_hold_sessions','instruction','reason')
        current=next((i for i in chain_items if i['chain']['ticker']==ticker and version and i['chain']['signal_date']==version['signal_date']),None)
        reviews=(current['chain'].get('reviews') or []) if current else []
        position.update({key:state.get(key) for key in fields})
        position.update(frozen_plan=frozen,current_chain={'state':current['state'], 'latest_review':reviews[-1] if reviews else None} if current else None)
    equity_rows=repository.equity_days(conn,aid,to_session=session,cutoff_at=now)
    metrics={'inception_return':accounts.inception_return(account,equity_rows),'max_drawdown':accounts.max_drawdown(equity_rows),
             'positions_count':len(positions),'max_positions':params['max_positions'],
             'available_positions':max(0,params['max_positions']-len(positions)-sum(o['action']=='buy' for o in pending))}
    return {'account':{**_public(conn,account),**metrics,'cutoff_at':now,'params':params,'cash':cash,'equity':equity['equity'] if equity else cash+sum((p['market_value'] for p in positions),ZERO)},
        'positions':positions,'pending_orders':pending,'buyable':candidates,
        'rejected_yesterday':[o for o in orders if o['status']=='rejected' and o['placed_on']==add_trading_days(session,-1)],
        'recent_closed':recent,'market':repository.regime_for(conn,session,cutoff_at=now) or {},'_orders':orders}


def _size_candidates(items, positions, pending, cash, params):
    pos_values={p['ticker']:p['market_value'] for p in positions}
    reserved={}
    for order in pending:
        if order['action']=='buy':
            reserved[order['ticker']]=reserved.get(order['ticker'],ZERO)+order['reserved_cash']
    for item in items:
        sized=paper_strategies.max_shares(item,equity=cash+sum(pos_values.values(),ZERO),params=params,cash=cash,
            positions_value_by_ticker=pos_values,total_positions_value=sum(pos_values.values(),ZERO),
            pending_reserved=reserved,positions_count=len(positions),pending_count=sum(o['action']=='buy' for o in pending))
        item.update(max_shares=sized['shares'],sizing_status=sized['status'])


def _save_decision(conn,account,*,session,now,run_id,output,facts,status='ok',reason=None,agent_run_id=None,evidence=None,skipped=None,host_cancellations=None):
    if evidence is None:
        evidence=portfolio_check.build_evidence({k:v for k,v in facts.items() if not k.startswith('_')})
    check=paper_strategies.validate_orders(output,items=facts['buyable'],positions=facts['positions'],
        pending_orders=facts['_orders'],cash=facts['account']['cash'],params=facts['account']['params']) if status=='ok' else {'accepted':[],'rejected':[]}
    if check.get('schema_error'): status,reason='rejected','schema_invalid'
    accepted=check['accepted'] if status=='ok' else []; rejected=check['rejected'] if status=='ok' else []
    row,inserted=repository.insert_decision(conn,{'account_id':account['id'],'session':session,'status':status,'status_reason':reason,
        'agent_run_id':agent_run_id,'agent_version':graph.PORTFOLIO_AGENT_VERSION if agent_run_id else None,
        'strategy_version':paper_strategies.FOLLOW_PLANS_VERSION if account['strategy']=='follow_plans' else None,
        'evidence':portfolio_check.evidence_facts(evidence) if isinstance(evidence,str) else evidence,'output':output,'summary':output.get('summary') if isinstance(output,dict) else None,
        'holds':output.get('holds') if isinstance(output,dict) else None,
        'detail':{'cutoff_at':now,'orders_placed':len(accepted),'orders_rejected':len(rejected),'skipped':skipped or [],
                  **({'host_cancellations':host_cancellations} if host_cancellations else {}),
                  **({k:repository.agent_run_by_id(conn,agent_run_id)[k] for k in ('input_tokens','output_tokens')} if agent_run_id else {})},'run_id':run_id})
    if not inserted: return row
    by_plan={str(i['plan_id']):i for i in facts['buyable']}
    by_order={str(o['id']):o for o in facts['_orders']}
    held={p['ticker']:p for p in facts['positions']}
    # Match the validator's cancellation-first budget semantics before UNIQUE pending-buy checks.
    accepted = sorted(accepted, key=lambda o: o['action'] != 'cancel')
    for index,(order,code) in enumerate([(o,None) for o in accepted]+[(r['order'],r['code']) for r in rejected]):
        item=by_plan.get(str(order.get('plan_id')),{})
        old=by_order.get(str(order.get('order_id')),{})
        ticker=item.get('ticker') or order.get('ticker') or old.get('ticker') or '?'
        action=order['action']; shares=order.get('shares')
        if action=='sell':
            shares=Decimal(str(held.get(ticker,{}).get('quantity',0)))
            if order.get('portion')=='half': shares=(shares/2).to_integral_value(rounding=ROUND_FLOOR)
        if code and action=='buy':
            try:
                shares=Decimal(str(shares))
                # Invalid raw intent remains in decision.output; its audit row must fit NUMERIC(18,6).
                shares=shares if shares.is_finite() and abs(shares)<Decimal('999999999999.999999') else ZERO
            except Exception: shares=ZERO
        status_='rejected' if code else ('cancelled' if action=='cancel' else 'pending')
        rec={'id':uuid5(NAMESPACE_URL,f"paper-order:{account['id']}:{session}:{index}"),'account_id':account['id'],
            'decision_id':row['id'],'action':action,'ticker':ticker,'plan_id':item.get('plan_id'),
            'chain_signal_date':item.get('chain_signal_date'),'shares':shares,'portion':order.get('portion'),
            'target_order_id':old.get('id'),'placed_on':session,'placed_by':'agent' if agent_run_id else 'host',
            'reason':order.get('reason'),'status':status_,'status_reason':code}
        repository.insert_order(conn,rec)
        if action=='cancel' and not code:
            repository.settle_order(conn,old['id'],status='cancelled',status_reason='agent_cancel' if agent_run_id else order['reason'],settled_on=session)
    return row


def decide_follow_plans(conn,account,*,session,now,run_id,host_cancellations=None):
    repository.lock(conn,f"account:{account['id']}")
    account=repository.account_by_id(conn,account['id'])
    if account['status']!='active' or account['opened_on']>session: return None
    existing=repository.decision_for(conn,account['id'],session)
    if existing: return existing
    facts=decision_facts(conn,account,session=session,now=now)
    intents=paper_strategies.follow_plans_orders(facts['buyable'],pending_orders=facts['_orders'],session=session)
    cancelled={str(o['order_id']) for o in intents['cancel']}
    pending=[o for o in facts['pending_orders'] if str(o['id']) not in cancelled]
    # Cancel against the complete candidate set first. Then select and size each
    # candidate using only actual reservations: a no-room skip occupies no ticker.
    # Agent output still uses the unchanged reject-without-resizing validator.
    placed=[]; skipped=[]
    occupied={o['ticker'] for o in pending if o['action']=='buy'}
    fee=Decimal(str(facts['account']['params']['fee_per_trade']))
    for item in facts['buyable']:
        if item['ticker'] in occupied:
            continue
        _size_candidates([item],facts['positions'],pending,facts['account']['cash'],facts['account']['params'])
        candidate=dict(item)
        if candidate.get('blocked_reason') in ('pending_chain','pending_ticker'):
            candidate['blocked_reason']=None  # the complete-set cancellation freed this ticker
        selected=paper_strategies.follow_plans_orders([candidate],pending_orders=facts['_orders'],session=session)
        skipped.extend(selected['skipped'])
        if not selected['place']:
            continue
        order=selected['place'][0]
        placed.append(order)
        occupied.add(item['ticker'])
        pending.append({**order,'reserved_cash':Decimal(order['shares'])*Decimal(str(item['entry_high']))+fee})
    orders=[{'action':'cancel','order_id':str(o['order_id']),'reason':o['reason']} for o in intents['cancel']]
    orders += [{'action':'buy','plan_id':str(o['plan_id']),'shares':o['shares'],'reason':o['reason']} for o in placed]
    output={'orders':orders,'holds':[{'ticker':p['ticker'],'reason':'按入场时计划管理'} for p in facts['positions']],
            'summary':'照计划执行'}
    return _save_decision(conn,account,session=session,now=now,run_id=run_id,output=output,facts=facts,skipped=skipped,
        host_cancellations=host_cancellations)


def decide_agent(conn,account,*,session,now,run_id,settings,deps,clock,started,host_cancellations=None):
    repository.lock(conn,f"account:{account['id']}")
    account=repository.account_by_id(conn,account['id'])
    if account['status']!='active' or account['opened_on']>session: return None
    existing=repository.decision_for(conn,account['id'],session)
    if existing: return existing
    if host_cancellations is None:
        host_cancellations=reconcile_pending_buys(conn,account,session=session,now=now)
    host_detail={'host_cancellations':host_cancellations} if host_cancellations else {}
    settings=settings or deps.agent_settings or model.settings_from_env()
    reason='stage_budget' if clock()-started>=ACCOUNTS_AGENT_DEADLINE_S else model.unavailable_reason(settings)
    if reason is None and settings.daily_tokens is not None and repository.agent_tokens_on(conn,now.astimezone(accounts.NY).date())>=settings.daily_tokens: reason='token_budget'
    if reason:
        return repository.insert_decision(conn,{'account_id':account['id'],'session':session,'status':'skipped',
            'status_reason':reason,'run_id':run_id,'detail':{'cutoff_at':now,**host_detail}})[0]
    facts=decision_facts(conn,account,session=session,now=now)
    evidence=portfolio_check.build_evidence({k:v for k,v in facts.items() if not k.startswith('_')}|{'secrets':[settings.api_key]})
    rid=str(uuid5(NAMESPACE_URL,f"portfolio:{account['id']}:{session}"))
    result={'input_tokens':0,'output_tokens':0,'calls':[],'output':None,'status':'error','reason':None}
    detail=None
    try:
        factory=deps.agent_checkpointer or (lambda:model.checkpointer(os.environ.get('INVESTMENT_ASSISTANT_DATABASE_URL','')))
        with factory() as checkpointer:
            ctx=tools.ToolContext(ticker='',session=session,cutoff_at=now,read=lambda fn:fn(conn))
            result=graph.run_portfolio(account=account,ctx=ctx,model=deps.agent_model if deps.agent_model is not None else model.chat_model(settings),
                checkpointer=checkpointer,run_id=rid,now=lambda:now,evidence=evidence)
    except Exception as exc:
        reason,detail=model.classify_error(exc,settings.api_key)
        result.update(status='error',reason=reason)
    run=service._agent_run_row({'ticker':None,'signal_date':None,'pool':None},settings=settings,attempt=1,now=now,
        status=result['status'],run_id=rid,mode='portfolio',agent_version=graph.PORTFOLIO_AGENT_VERSION,
        reason=result.get('reason'),detail=detail,output=result.get('output'),input_tokens=result.get('input_tokens') or 0,
        output_tokens=result.get('output_tokens') or 0)
    repository.insert_agent_run(conn,run,result.get('calls') or [])
    status='ok' if result['status']=='ok' else ('rejected' if result['status']=='rejected' else 'skipped')
    try:
        with conn.transaction():
            return _save_decision(conn,account,session=session,now=now,run_id=run_id,output=result.get('output'),facts=facts,
                status=status,reason='schema_invalid' if status=='rejected' else result.get('reason'),agent_run_id=rid,evidence=evidence,
                host_cancellations=host_cancellations)
    except Exception as exc:
        _,safe=model.classify_error(exc,settings.api_key)
        return repository.insert_decision(conn,{'account_id':account['id'],'session':session,'status':'skipped',
            'status_reason':'host_error','agent_run_id':rid,'agent_version':graph.PORTFOLIO_AGENT_VERSION,
            'evidence':portfolio_check.evidence_facts(evidence),'output':result.get('output'),'run_id':run_id,
            'detail':{'cutoff_at':now,'error':safe,'input_tokens':run['input_tokens'],'output_tokens':run['output_tokens'],**host_detail}})[0]


def _skip_decision(conn,account,*,session,now,run_id,reason,host_cancellations=None):
    repository.lock(conn,f"account:{account['id']}")
    current=repository.account_by_id(conn,account['id'])
    if current['status']!='active' or current['opened_on']>session: return None
    existing=repository.decision_for(conn,account['id'],session)
    if existing: return existing
    if host_cancellations is None:
        host_cancellations=reconcile_pending_buys(conn,current,session=session,now=now)
    return repository.insert_decision(conn,{'account_id':account['id'],'session':session,'status':'skipped',
        'status_reason':reason,'run_id':run_id,'detail':{'cutoff_at':now,
        **({'host_cancellations':host_cancellations} if host_cancellations else {})}})[0]


def run_stage(session,*,now,deps,run_id,acc,settings):
    if not is_trading_day(session): return {'accounts':[],'skipped':'not_trading_day'}
    clock=deps.clock or clock_module.monotonic; started=clock()
    with core_tx._tx() as conn:
        require_ready(conn)
        active=[a for a in repository.list_accounts(conn) if a['opened_on'] and a['opened_on']<=session]
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
        out=phase(account,settle_account,through=session,now=now,run_id=run_id)
        if out:
            results[str(account['id'])].update(out)
            if out.get('equity') and out['equity']['cash']<0:
                # Settlement and protective exits stay valid; cash validation prevents new buys.
                results[str(account['id'])]['error']='negative_cash'
                if acc: acc.fail(account['name'],'negative_cash')
    try:
        with core_tx._tx() as conn: real=write_real_equity(conn,session=session,now=now,run_id=run_id)
        if str(REAL_ACCOUNT_ID) in results: results[str(REAL_ACCOUNT_ID)]['equity']=real
    except Exception as exc:
        failed.add(str(REAL_ACCOUNT_ID))
        if str(REAL_ACCOUNT_ID) in results: results[str(REAL_ACCOUNT_ID)]['error']=families.sanitize_reason(exc)
        if acc: acc.fail('真实账户', families.sanitize_reason(exc))
    for account in papers:
        if str(account['id']) not in failed:
            held=phase(account,write_paper_holdings,session=session,now=now,run_id=run_id)
            results[str(account['id'])]['holdings']=held or []
    host_cancellations={}
    for account in papers:
        aid=str(account['id'])
        if aid not in failed:
            changed=phase(account,reconcile_pending_buys,session=session,now=now)
            host_cancellations[aid]=changed or []
            results[aid]['settled']['cancelled']+=len(changed or [])
    unavailable=None
    for strategy in ('follow_plans','agent_managed'):
        for account in papers:
            if account['strategy']!=strategy or str(account['id']) in failed: continue
            kw={'session':session,'now':now,'run_id':run_id,'host_cancellations':host_cancellations.get(str(account['id']),[])}
            if strategy=='agent_managed' and unavailable:
                row=phase(account,_skip_decision,reason=unavailable,**kw)
            else:
                if strategy=='agent_managed': kw.update(settings=settings,deps=deps,clock=clock,started=started)
                row=phase(account,decide_follow_plans if strategy=='follow_plans' else decide_agent,**kw)
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
    if not repository.accounts_ready(conn):
        return ['migration_030_missing']
    runs=[r for r in repository.stage_runs_for_session(conn,session)
          if r['job']=='daily' and r['stage']=='accounts' and r['status']!='skipped']
    if not runs: return []
    ended=max(r['finished_at'] for r in runs)
    problems=[]
    for account in repository.list_accounts(conn):
        if not account['opened_on'] or account['opened_on']>session or account['created_at']>ended: continue
        rows=repository.equity_days(conn,account['id'],from_session=session,to_session=session)
        if not rows: problems.append(f"{account['name']}：{session} missing equity")
        if account['kind']=='paper' and repository.decision_for(conn,account['id'],session) is None:
            problems.append(f"{account['name']}：{session} missing decision")
    return problems


def main(argv=None):
    parser=argparse.ArgumentParser(description='账户与模拟交易概览')
    sub=parser.add_subparsers(dest='command',required=True)
    show=sub.add_parser('show'); show.add_argument('--account'); show.add_argument('--session',type=date.fromisoformat)
    args=parser.parse_args(argv)
    from investment_assistant.workbench import account_views
    now=datetime.now(UTC)
    session=args.session or bars.latest_closed_session(now)
    rows=account_views.overview(now=now,session=session,include_closed=True)['accounts']
    if args.account: rows=[r for r in rows if str(r['id'])==args.account or r['name']==args.account]
    print('账户\t策略\t状态\t权益\t结算至')
    for row in rows: print(f"{row['name']}\t{row['strategy_text']}\t{row['status']}\t{(row['latest'] or {}).get('equity','—')}\t{row['settled_through'] or '—'}")
    actions={'buy':'买入','sell':'卖出','cancel':'撤单'}
    statuses={'ok':'成功','skipped':'跳过','rejected':'未通过校验'}
    for row in rows:
        detail=account_views.session_detail(row['id'],now=now,session=session)
        print(f"\n{row['name']} · {session}")
        print('持仓\n标的\t股数\t平均成本')
        for position in detail['positions']:
            print(f"{position['ticker']}\t{position['quantity']}\t{position['avg_cost']}")
        if not detail['positions']: print('无持仓')
        print('待成交\n标的\t动作\t股数\t计划\t下单日\t理由')
        for order in detail['pending_orders']:
            print(f"{order['ticker']}\t{actions.get(order['action'],order['action'])}\t{order.get('shares') or '—'}\t{order.get('plan_id') or '—'}\t{order['placed_on']}\t{order.get('reason') or '—'}")
        if not detail['pending_orders']: print('无待成交订单')
        print('当日决策\n交易日\t状态\t总结')
        decision=detail['decision']
        if decision is None:
            print('当日无决策记录')
            continue
        print(f"{decision['session']}\t{statuses.get(decision['status'],decision['status'])}\t{decision.get('summary') or '—'}")
        if decision.get('status_reason'): print(f"原因：{decision['status_reason']}")
        for order in decision.get('orders') or []:
            print(f"{actions.get(order['action'],order['action'])}\t{order['ticker']}\t{order.get('shares') or '—'}\t{order.get('reason') or '—'}\t{order['host_check']}\t{order['result_text']}")
        for held in decision.get('holds') or []:
            print(f"继续持有\t{held['ticker']}\t{held['reason']}")
    return 0


if __name__=='__main__': raise SystemExit(main())
