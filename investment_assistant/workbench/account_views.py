"""Read-only account projections: ledger positions, settlement evidence and comparable metrics."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.workbench import (accounts, bars, monthly, paper_accounts, paper_fill, paper_strategies,
                                               repository, review, service)

CALC_VERSIONS={'account':accounts.ACCOUNT_VERSION,'paper_fill':paper_fill.PAPER_FILL_VERSION,
               'monthly':monthly.MONTHLY_VERSION,'discipline':'discipline-2','tracks':'tracks-2'}


def _rows(conn,account,session,now):
    return repository.equity_days(conn,account['id'],to_session=session,cutoff_at=now)


def account_row(conn,account,*,session,now):
    rows=_rows(conn,account,session,now)
    return {**paper_accounts._public(conn,account),'latest':rows[-1] if rows else None,
            'opening_session': accounts.opening_session(account),
            'inception_return':accounts.inception_return(account,rows),'max_drawdown':accounts.max_drawdown(rows)}


def overview(*,now,session=None,include_closed=False):
    session=session or bars.latest_closed_session(now)
    with service.read_only_tx() as conn:
        paper_accounts.require_ready(conn)
        return service._jsonable({'accounts':[account_row(conn,a,session=session,now=now)
            for a in repository.list_accounts(conn,include_closed=include_closed)]})


def _positions(conn,account,*,session,now):
    from investment_assistant.workbench import plan_review
    trades,splits,books=paper_accounts.account_books(conn,account['id'],session=session,now=now)
    spy=repository.read_bars(conn,'SPY',session,cutoff_at=now,limit=10000)
    held=[]; closed=[]
    for ticker,book in books.items():
        prices=repository.read_bars(conn,ticker,session,cutoff_at=now,limit=10000)
        closed.extend({'ticker':ticker,**review.trade_segment_review(s,prices,spy)} for s in book['closed'])
        if not book['position']: continue
        p=book['position']; history=repository.holding_status_for(conn,account_id=account['id'],ticker=ticker)
        status=next((r for r in reversed(history) if r['session']<=session and r['session']>=p['opened_at'].astimezone(accounts.NY).date()),None)
        if status is None:
            status=plan_review._holding_row(conn,ticker,session,None,now=now,skipped={},unavailable=None,account_id=account['id']) or {}
        close=Decimal(str(prices[-1]['close'])) if prices else None
        plan=repository.plan_by_id(conn,status['plan_id']) if status.get('plan_id') else None
        version=repository.version_by_plan(conn,plan['id']) if plan else None
        keys=('r_now','stop','stop_mode','target1','to_stop_pct','to_t1_pct','held_sessions','max_hold_sessions','instruction','reason')
        held.append({'ticker':ticker,**p,'last_close':close,'market_value':p['quantity']*close if close is not None else None,
            'unrealized_pnl':p['quantity']*(close-p['avg_cost']) if close is not None else None,
            **{k:status.get(k) for k in keys},'plan':{'plan_id':plan['id'],
            'label':'Agent 计划' if plan['kind']=='agent' else ('自定计划' if plan['kind']=='own' else '规则基线'),
            'chain_signal_date':version['signal_date'] if version else None,'version_no':version['version_no'] if version else None} if plan else None})
    return held,closed


def _decision(conn,row,*,full=False):
    if row is None: return None
    orders=[o for o in repository.orders_for(conn,row['account_id'],limit=100000) if o['decision_id']==row['id']]
    data={k:row.get(k) for k in ('id','session','status','status_reason','summary')}
    data['cutoff_at']=(row.get('detail') or {}).get('cutoff_at')
    data.update(orders_placed=(row.get('detail') or {}).get('orders_placed',sum(o['status']!='rejected' for o in orders)),
                orders_rejected=(row.get('detail') or {}).get('orders_rejected',sum(o['status']=='rejected' for o in orders)))
    if full:
        evidence=row.get('evidence')
        if isinstance(evidence,str):
            from investment_assistant.workbench.agent.portfolio_check import evidence_facts
            evidence=evidence_facts(evidence)
        data.update(evidence=evidence,output=row.get('output'),holds=row.get('holds'),orders=[_order(conn,o) for o in orders])
    return data


def _order(conn,row):
    trade=repository.trade_by_id(conn,row['trade_id'],account_id=row['account_id']) if row['status']=='filled' and row.get('trade_id') else None
    texts={'rejected':paper_strategies.REJECT_TEXT,'voided':paper_fill.VOID_REASON_TEXT}.get(row['status'],paper_fill.CANCEL_REASON_TEXT)
    reason=texts.get(row['status_reason'],row['status_reason'])
    return {**row,'host_check':reason if row['status']=='rejected' else '通过校验','result_text':accounts.ORDER_STATUS_TEXT[row['status']]+(f'：{reason}' if reason else ''),
            'filled_at':trade['executed_at'] if trade else None,'filled_price':trade['price'] if trade else None,
            'filled_quantity':trade['quantity'] if trade else None}


def detail(account_id,*,now):
    session=bars.latest_closed_session(now)
    with service.read_only_tx() as conn:
        account=paper_accounts.require_account(conn,account_id)
        public=account_row(conn,account,session=session,now=now)
        held,closed=_positions(conn,account,session=session,now=now)
        decisions=repository.decisions_for(conn,account['id']) if account['kind']=='paper' else []
        row=next((d for d in decisions if d['session']<=session),None)
        snapshots=[s for s in repository.list_snapshots(conn,account_id=account['id']) if s['recorded_at']<=now]
        return service._jsonable({'account':public,'equity':public['latest'],'positions':held,'closed_positions':closed,
            'pending_orders':[_order(conn,o) for o in repository.pending_orders(conn,account['id'])],
            'decision':_decision(conn,row),'reconciliation':accounts.reconcile(snapshots,_rows(conn,account,session,now),public['params']) if account['kind']=='real' else None,
            'forms':{'can_record_trades':account['kind']=='real' and account['status']=='active'},'cutoff_at':now,'calc_versions':CALC_VERSIONS})


def equity(account_id,*,now,from_session=None,to_session=None):
    end=min(to_session or bars.latest_closed_session(now),bars.latest_closed_session(now))
    with service.read_only_tx() as conn:
        account=paper_accounts.require_account(conn,account_id)
        rows=repository.equity_days(conn,account['id'],from_session=from_session,to_session=end,cutoff_at=now)
        days={r['session'] for r in rows}
        spy=[{'session':b['price_date'],'close':b['adj_close']} for b in repository.read_bars(conn,'SPY',end,cutoff_at=now,limit=100000) if b['price_date'] in days]
        return service._jsonable({'rows':rows,'spy':spy})


def trades(account_id,*,now):
    from investment_assistant.workbench import plan_views
    with service.read_only_tx() as conn:
        account=paper_accounts.require_account(conn,account_id)
        rows=list(reversed(repository.list_trades(conn,account_id=account['id'],cutoff=now)))
        labels=plan_views.trade_plan_labels(conn,rows)
        return service._jsonable({'trades':[{**r,'plan_label':labels.get(r['id'])} for r in rows]})


def orders(account_id,*,now,status=None):
    if status is not None and status not in accounts.ORDER_STATUS_TEXT: raise ValueError('unknown order status')
    with service.read_only_tx() as conn:
        account=paper_accounts.require_account(conn,account_id)
        return service._jsonable({'orders':[_order(conn,o) for o in repository.orders_for(conn,account['id'],status=status)]})


def decisions(account_id,*,now,session=None):
    with service.read_only_tx() as conn:
        account=paper_accounts.require_account(conn,account_id)
        if session:
            return service._jsonable({'decision':_decision(conn,repository.decision_for(conn,account['id'],session),full=True)})
        return service._jsonable({'decisions':[_decision(conn,d) for d in repository.decisions_for(conn,account['id'])]})


def session_detail(account_id, *, now, session):
    """Read-only CLI facts for one session using information known at ``now``.

    Trades are cut by execution session. An order settled after the selected day
    was still pending then; its later outcome remains visible in decision detail.
    """
    with service.read_only_tx() as conn:
        account = paper_accounts.require_account(conn, account_id)
        _, _, books = paper_accounts.account_books(conn, account['id'], session=session, now=now)
        held = [{'ticker': ticker, **book['position']} for ticker, book in sorted(books.items()) if book['position']]
        pending = []
        for order in reversed(repository.orders_for(conn, account['id'], limit=100000)):
            if order['placed_on'] > session or order['action'] == 'cancel':
                continue
            if order['status'] == 'pending' or order.get('settled_on') and order['settled_on'] > session:
                pending.append(_order(conn, {**order, 'status': 'pending', 'status_reason': None,
                    'settled_on': None, 'trade_id': None}))
        return service._jsonable({'positions': held, 'pending_orders': pending,
            'decision': _decision(conn, repository.decision_for(conn, account['id'], session), full=True)})


def _pct(value): return '—' if value is None else f'{value:+.1%}'


def _month_to_date(session, *, equity_rows, snapshots, flows, opened):
    """Read-view return over exact shared dates; frozen monthly-5 reports stay unchanged.

    A current-month view ends at the cutoff session, not the future month end.
    A complete ledger pair takes precedence. Snapshot fallback also needs both
    exact dates, so its period remains comparable with the SPY column.
    """
    first = add_trading_days(session.replace(day=1), -1)
    by_day = {row['session']: row for row in equity_rows} if opened else {}
    start, end = by_day.get(first), by_day.get(session)
    incomplete = any((row.get('detail') or {}).get('bars_missing') or
                     (row.get('detail') or {}).get('stale') for row in (start, end) if row)
    source = 'ledger'
    if start is None or end is None or incomplete:
        superseded = {row.get('supersedes_id') for row in snapshots}
        live = sorted((row for row in snapshots if row['id'] not in superseded),
                      key=lambda row: row['recorded_at'])
        by_day = {row['as_of']: row for row in live}
        start, end = by_day.get(first), by_day.get(session)
        source = 'snapshot'
    if start is None or end is None:
        return {'return': None, 'note': '端点估值不完整' if incomplete else '缺少区间端点'}
    initial = Decimal(str(start['equity']))
    if initial <= 0:
        return {'return': None, 'note': '起点权益非正数'}
    net = sum((Decimal(str(flow['amount'])) for flow in flows
               if flow.get('voided_at') is None and first < flow['flow_date'] <= session), Decimal(0))
    return {'return': float((Decimal(str(end['equity'])) - initial - net) / initial), 'note': source}


def compare(*,now,from_session=None):
    session=bars.latest_closed_session(now)
    keys=('inception_return','month_return','max_drawdown','closed_and_win_rate','mean_r','discipline','skipped_plans','positions_and_pending')
    labels=('累计收益','本月收益','最大回撤','平仓数 / 胜率','平均 R','纪律','未执行计划','持仓 / 待成交')
    with service.read_only_tx() as conn:
        paper_accounts.require_ready(conn)
        columns=[]; values={key:[] for key in keys}
        for account in repository.list_accounts(conn):
            aid=account['id']; start=from_session or account['opened_on'] or session-timedelta(days=90)
            rows=_rows(conn,account,session,now); pub=account_row(conn,account,session=session,now=now)
            held,closed=_positions(conn,account,session=session,now=now)
            closed=[s for s in closed if date.fromisoformat(s['closed'])>=start]
            trades_,splits,books=paper_accounts.account_books(conn,aid,session=session,now=now)
            flows=paper_accounts.known_flows(conn,aid,now)
            snapshots=[s for s in repository.list_snapshots(conn,account_id=aid) if s['recorded_at']<=now]
            ret=_month_to_date(session,equity_rows=rows,snapshots=snapshots,flows=flows,opened=pub['opened'])
            disc=service._discipline_v2(conn,trades=[t for t in trades_ if t['executed_at'].astimezone(accounts.NY).date()>=start],
                segments=[s for b in books.values() for s in b['closed']],tickers=list(books),session=session,now=now,since=start,account_id=aid)
            track_rows=service._track_rows(conn,start,session,session=session,now=now,account_id=aid)
            rs=[r['actual']['r'] for r in track_rows if r['actual'].get('status')=='closed' and r['actual'].get('r') is not None]
            pending=repository.pending_orders(conn,aid)
            skipped=sum(r['actual'].get('status')=='not_executed' and r['agent'].get('plan_id') is not None for r in track_rows)
            if account['strategy']=='follow_plans':
                skipped=sum(len((d.get('detail') or {}).get('skipped',[])) for d in repository.decisions_for(conn,aid,limit=100000) if start<=d['session']<=session)
            wins=sum(s['realized_pnl']>0 for s in closed); winrate=wins/len(closed) if closed else None
            counts=disc['counts']
            sample=len(disc.get('trades',[])); draw=pub['max_drawdown']
            def put(key,value,text=None,note=None): values[key].append({'value':value,'text':text if text is not None else str(value) if value is not None else '—','note':note})
            put('inception_return',pub['inception_return'],_pct(pub['inception_return']))
            put('month_return',ret.get('return'),_pct(ret.get('return')),ret.get('note'))
            put('max_drawdown',draw['pct'] if draw else None,f"{draw['pct']:.1%}" if draw else '—')
            put('closed_and_win_rate',{'closed':len(closed),'win_rate':winrate},f'{len(closed)} / '+(_pct(winrate) if winrate is not None else '—'))
            put('mean_r',sum(rs)/len(rs) if len(rs)>=5 else None,f'{sum(rs)/len(rs):.2f} R' if len(rs)>=5 else '样本 < 5',f'样本 {len(rs)}')
            put('discipline',counts if sample else None,
                ' / '.join(f'{label} {counts[key]}' for key,label in [('unplanned','计划外'),('chase','追高'),('early_take','早卖'),('held_loser','死扛')]) if sample else '—',f'买入样本 {sample}')
            put('skipped_plans',skipped,str(skipped))
            put('positions_and_pending',{'positions':len(held),'pending':len(pending)},f'{len(held)} / {len(pending)}')
            columns.append({'account_id':aid,'name':account['name'],'strategy':account['strategy']})
        benchmark=repository.read_bars(conn,'SPY',session,cutoff_at=now,limit=100000)
        since=from_session or min((a['opened_on'] for a in repository.list_accounts(conn) if a['opened_on']),default=session)
        points=[b for b in benchmark if b['price_date']>=since]
        spy_return=float(points[-1]['adj_close']/points[0]['adj_close']-1) if len(points)>1 and points[0]['adj_close'] else None
        month_start=add_trading_days(session.replace(day=1),-1)
        month_points={b['price_date']:b for b in benchmark if month_start<=b['price_date']<=session}
        first, last = month_points.get(month_start), month_points.get(session)
        spy_month=float(last['adj_close']/first['adj_close']-1) if first and last and first['adj_close']>0 and last['adj_close']>0 else None
        spy_rows=[{'session':b['price_date'],'equity':Decimal(str(b['adj_close']))} for b in points]
        dd=accounts.max_drawdown(spy_rows)
        spy={'inception_return':_pct(spy_return),'month_return':_pct(spy_month),'max_drawdown':f"{dd['pct']:.1%}" if dd else '—'}
        return service._jsonable({'columns':columns,'rows':[{'key':k,'label':label,'values':values[k],'spy':spy.get(k)} for k,label in zip(keys,labels)],
            'cutoff_at':now,'calc_versions':CALC_VERSIONS})
