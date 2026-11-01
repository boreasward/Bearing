from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from investment_assistant.config import load_config
from investment_assistant.core import schema as core_schema, stages as stages_module, redact as families, tx as core_tx
from investment_assistant.core.constants import REAL_ACCOUNT_ID
from investment_assistant.core.tx import Unavailable, _jsonable
from investment_assistant.workflow.state import _moment, _stage_finish, _stage_skip
from investment_assistant.data.adapters import bars
from investment_assistant.execution import ledger, repo as execution_repo
from investment_assistant.review import monthly, repo as review_repo, weekly as review_weekly, period_digest as review_digest

def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None, *,
                ticks: bool = True) -> dict[str, Any]:
    """The ``retro`` node (v2.8 D2): on a trading day the review cards (v2.7 §9: detect, freeze, draft — in its own
    1500-second window, D24), then on any day the weekly and monthly review when due (each in its own ``try``, the
    v2.7 ``review_tick`` parts; a manual run generates but does not push). Neither done → ``skipped / not_due``.
    ``ticks=False`` is the v2.7 ``run_batch`` shell: the cards only."""
    from investment_assistant.review import retro_cards

    now, session = _moment(state)
    summary = state['summary']
    window = ctx.window_start if ctx.window_start is not None else ctx.clock()
    growth = state.get('growth_ready')
    if growth is None:
        with core_tx._tx() as conn:
            growth = core_schema.growth_ready(conn)
    cards = bool(state.get('trading_day', True))
    if cards and not growth:
        summary['errors'].append('growth: migration_027_missing')
        _stage_skip(ctx, 'retro', 'migration_027_missing')
        return {'summary': summary}
    card_failure: str | None = None
    if cards:
        try:
            retro_cards.run_stage(session, now=now, deps=ctx.deps, summary=summary, started=window,
                                  clock=ctx.clock, run_id=state.get('run_id'), acc=acc)
        except Unavailable:
            raise
        except Exception as exc:
            reason = families.sanitize_reason(exc)
            summary['errors'].append(f'retro: {reason}')
            card_failure = reason or type(exc).__name__
    if not ticks:
        if card_failure is not None:
            _stage_finish(ctx, 'retro', failed=card_failure)
        return {'summary': summary}
    acted = False
    manual = state.get('trigger') == 'manual_batch'
    out: dict[str, Any] = {'weekly': None, 'monthly': None, 'errors': []}
    for name, part in (('weekly', _tick_weekly), ('monthly', _tick_monthly)):
        try:
            out[name] = part(now=now, notify_cfg=ctx.notify_cfg, send=ctx.send, trigger_source='manual' if manual else 'scheduled',
                             client=None, push=not manual)
        except Unavailable:
            raise
        except Exception as exc:
            reason = families.sanitize_reason(exc)
            out[name] = {'action': 'error', 'error': reason}
            out['errors'].append(f'review {name}: {reason}')
            if acc is not None:
                acc.fail(name, reason)
        if out[name].get('action') in ('generated', 'reused', 'reminded'):
            acted = True
    summary.setdefault('errors', []).extend(out['errors'])
    summary['review_tick'] = _jsonable(out)
    if acc is not None:
        acc.detail['cards'] = cards
        acc.detail['weekly'] = summary['review_tick']['weekly']
        acc.detail['monthly'] = summary['review_tick']['monthly']
    if card_failure is not None:        # the row is written once, after the weekly / monthly part has set its detail
        _stage_finish(ctx, 'retro', failed=card_failure)
    elif not cards and not acted and not out['errors']:
        _stage_skip(ctx, 'retro', 'not_due')
    return {'summary': summary}

def _redact_reason(reason: str, notify_cfg) -> str:
    """Webhook URLs never survive into stored push reasons."""
    from investment_assistant.core.redact import redact

    for secret in (getattr(notify_cfg, 'webhooks', None) or {}).values():
        if secret:
            reason = redact(reason, str(secret))
    return reason

def _record_push(kind, period, *, review_id, trigger_source, result, notify_cfg, conn=None) -> dict[str, Any]:
    row = {'kind': kind, 'period': period, 'review_id': review_id, 'trigger_source': trigger_source,
           'sent': bool(result.get('sent')), 'messages': result.get('messages') or 0,
           'reason': _redact_reason(result['reason'], notify_cfg) if result.get('reason') else None}
    with core_tx._tx() as conn2:
        review_repo.insert_review_push(conn2, row)
    return {'kind': kind, 'period': period, 'review_id': review_id, 'sent': row['sent'],
            'messages': row['messages'], 'reason': row['reason']}

def push_review(kind: str, period: date, *, now: datetime, notify_cfg, send, trigger_source: str = 'manual',
                client=None) -> dict[str, Any]:
    """Push one period's latest frozen snapshot and record the attempt (§6.3).
    Manual pushes resend regardless of a previous success."""
    if kind not in ('weekly', 'monthly'):
        raise ledger.LedgerError('kind 必须是 weekly 或 monthly')
    if kind == 'weekly' and period.weekday() != 0:
        raise ledger.LedgerError('period 必须是周一')
    if kind == 'monthly' and period.day != 1:
        raise ledger.LedgerError('period 必须是当月 1 日')
    public_url = load_config().workbench.public_url
    with core_tx._tx() as conn:
        head = (review_repo.weekly_head(conn, period) if kind == 'weekly'
                else review_repo.monthly_head(conn, period))
    if head is None:
        raise ledger.NotFound(f'{period} 没有该周期的复盘快照')
    payload = (review_digest.weekly(head, public_url=public_url) if kind == 'weekly'
               else review_digest.monthly(head, public_url=public_url))
    result = send(kind, payload, notify_cfg, client=client)
    return _record_push(kind, period, review_id=head['id'], trigger_source=trigger_source,
                        result=result, notify_cfg=notify_cfg)

def _tick_weekly(*, now: datetime, notify_cfg, send, trigger_source: str, client, push: bool = True) -> dict[str, Any]:
    """§6.1 weekly: reuse a snapshot frozen after the week's final close, else
    generate one (superseding whatever is there), then push."""
    week = review_digest.due_week(now)
    week_end = week + timedelta(days=6)
    with core_tx._tx() as conn:
        if review_repo.review_push_sent(conn, 'weekly', week):
            return {'period': week, 'action': 'already_sent'}
        head = review_repo.weekly_head(conn, week)
    final = review_digest.last_session(week, week_end)
    fresh = head is not None and final is not None and bars.latest_closed_session(head['created_at']) >= final
    if fresh:
        action = 'reused'
    else:
        head = review_weekly.generate_weekly(week, notes='自动生成', supersedes_id=head['id'] if head else None, now=now)
        action = 'generated'
    if not push:        # v2.8 §13.3: a manual run generates the snapshot but never pushes
        return {'period': week, 'action': action, 'push': 'manual_run'}
    sent = push_review('weekly', week, now=now, notify_cfg=notify_cfg, send=send, trigger_source=trigger_source,
                       client=client)
    return {'period': week, 'action': action, 'push': sent}

def _tick_monthly(*, now: datetime, notify_cfg, send, trigger_source: str, client, push: bool = True) -> dict[str, Any]:
    """With ledger month endpoints or a broker month-end snapshot, reuse-or-generate then push; without either,
    remind once from the first Saturday of the next month on — unless the month predates
    the workbench (no decision or account snapshot on or before its last day)."""
    month = review_digest.due_month(now)
    month_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    with core_tx._tx() as conn:
        if review_repo.review_push_sent(conn, 'monthly', month):
            return {'period': month, 'action': 'already_sent'}
        reminded_before = review_repo.review_push_sent(conn, 'monthly_reminder', month)
        snapshots, flows = execution_repo.list_snapshots(conn), execution_repo.list_flows(conn)
        head = review_repo.monthly_head(conn, month)
        account_recorded = execution_repo.latest_account_record_at(conn)
        started_on = review_repo.workbench_started_on(conn)
        real = execution_repo.account_by_id(conn, REAL_ACCOUNT_ID) if core_schema.accounts_ready(conn) else None
        equity_rows = execution_repo.equity_days(conn, REAL_ACCOUNT_ID, cutoff_at=now) if real else []
        ledger_ready = monthly.month_return_v2(month, equity_rows=equity_rows, snapshots=snapshots, flows=flows,
                                               opened=bool(real and real['opened_on'])).get('source') == 'ledger'
        if ledger_ready:
            account_recorded = max([r['computed_at'] for r in equity_rows]
                                   + ([account_recorded] if account_recorded else []))
    if not ledger_ready and monthly.month_end_snapshot(snapshots, month) is None:
        if started_on is None or started_on > month_end:
            return {'period': month, 'action': 'before_start'}
        if reminded_before:
            return {'period': month, 'action': 'waiting'}
        if not review_digest.reminder_due(month, now):
            return {'period': month, 'action': 'waiting'}
        if not push:
            return {'period': month, 'action': 'waiting', 'push': 'manual_run'}
        payload = review_digest.reminder(month, public_url=load_config().workbench.public_url)
        result = send('reminder', payload, notify_cfg, client=client)
        push = _record_push('monthly_reminder', month, review_id=None, trigger_source=trigger_source,
                            result=result, notify_cfg=notify_cfg)
        return {'period': month, 'action': 'reminded', 'push': push}
    final = review_digest.last_session(month, month_end)
    fresh = (head is not None and final is not None
             and bars.latest_closed_session(head['created_at']) >= final
             and (account_recorded is None or head['created_at'] > account_recorded))
    if fresh:
        action = 'reused'
    else:
        head = review_weekly.generate_monthly(month, notes='自动生成', supersedes_id=head['id'] if head else None, now=now)
        action = 'generated'
    if not push:
        return {'period': month, 'action': action, 'push': 'manual_run'}
    sent = push_review('monthly', month, now=now, notify_cfg=notify_cfg, send=send, trigger_source=trigger_source,
                       client=client)
    return {'period': month, 'action': action, 'push': sent}

def review_tick(*, now: datetime, notify_cfg, send, trigger_source: str, client=None) -> dict[str, Any]:
    """The workbench_review task body (§6.1): weekly and monthly each in their own
    transaction; one failing never blocks the other, and errors land in the report."""
    out: dict[str, Any] = {'weekly': None, 'monthly': None, 'errors': []}
    for name, part in (('weekly', _tick_weekly), ('monthly', _tick_monthly)):
        try:
            out[name] = part(now=now, notify_cfg=notify_cfg, send=send, trigger_source=trigger_source, client=client)
        except Unavailable:
            raise
        except Exception as exc:
            out[name] = {'action': 'error', 'error': str(exc)}
            out['errors'].append(f'review {name}: {exc}')
    return out

def list_review_pushes(limit: int = 20) -> list[dict[str, Any]]:
    with core_tx._tx() as conn:
        rows = review_repo.list_review_pushes(conn, limit)
    return [{**row, 'id': str(row['id']), 'review_id': str(row['review_id']) if row['review_id'] else None,
             'period': row['period'].isoformat(), 'created_at': row['created_at'].isoformat()}
            for row in rows]
