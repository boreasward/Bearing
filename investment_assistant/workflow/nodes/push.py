from __future__ import annotations

from datetime import date, datetime
from typing import Any

from investment_assistant.config import load_config
from investment_assistant.core import schema as core_schema, stages as stages_module, redact as families, tx as core_tx
from investment_assistant.core.constants import NY
from investment_assistant.workflow import recorder, repo as workflow_repo
from investment_assistant.workflow.state import _moment, _wall, _stage_skip, feature_enabled
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.execution import chains as execution_chains, repo as execution_repo
from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.review import repo as review_repo, push_digest as digest_module
from investment_assistant.views.today import unit_cards, _brief_view, _agent_view, portfolio_check_view, PORTFOLIO_FLAG

def account_push(recorder: recorder.StageRecorder | None, acc: stages_module.StageAcc,
                 out: dict[str, Any]) -> None:
    """Close the push stage from the notifier's result (v2.4 §4): a skip for the two non-events, success for sent /
    no_notifications, failed for every other reason.

    The notifier reports only the messages actually sent, so input equals output here."""
    reason = out.get('reason')
    if not out.get('sent') and reason in ('already_pushed', 'task_disabled'):
        if recorder is not None:
            recorder.skip('push', reason)
        return
    messages = int(out.get('messages') or 0)
    acc.input(messages)
    acc.output(messages)
    acc.set_ref('push', {'sent': bool(out.get('sent')), 'messages': messages,
                         'reason': families.sanitize_reason(reason) if reason else None})
    if recorder is not None:
        if out.get('sent') or reason == 'no_notifications':
            recorder.finish('push')
        else:
            recorder.finish('push', failed=str(reason or 'not_sent'))

def retro_push_counts(*, now: datetime) -> dict[str, int]:
    """The push overview's retro-card line (v2.8 D3): cards created since the last ``daily`` push and the cards waiting
    for review now. Without an earlier ``daily`` push nothing counts as new."""
    from investment_assistant.review import retro_cards

    with core_tx.read_only_tx() as conn:
        since = workflow_repo.latest_daily_push_finished_at(conn, before=now)
        cards = review_repo.review_cards(conn)
        new = sum(1 for card in cards if since is not None and card['created_at'] > since)
        to_review = sum(1 for card in cards if retro_cards.card_state(conn, card['id'])['status'] == 'to_review')
    return {'new_cards': new, 'to_review': to_review}

def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``push`` node (from the v2.7 ``workbench`` task): the day's digest, or the one holiday line; a manual run
    does not push. The retro-card line counts cards since the last ``daily`` push; the digest carries a line for
    every family that fetched partially or failed."""
    now, _ = _moment(state)
    summary = state['summary']
    if state.get('trigger') == 'manual_batch':
        _stage_skip(ctx, 'push', 'manual_run')       # 手动运行不推送
        return {'summary': summary}
    closed = summary.get('status') == 'skipped'
    if closed:
        acc.detail['closed'] = True
    with acc.timing():
        if closed:
            summary['push'] = push_closed(now=now, notify_cfg=ctx.notify_cfg, send=ctx.send)
        else:
            summary['push'] = push(now=now, force=False, summary=summary, notify_cfg=ctx.notify_cfg, send=ctx.send,
                                   retro_counts=retro_push_counts(now=_wall(ctx)()),
                                   fetch_status=(state.get('refs') or {}).get('collect', {}).get('families'),
                                   features=state.get('features'))
    account_push(ctx.recorder, acc, summary['push'])
    return {'summary': summary}

def _last_push_failed(conn, session: date) -> bool:
    last = workflow_repo.last_push(conn)
    return bool(last and last.get('session') == session.isoformat() and last.get('sent') is False
                and last.get('reason') not in (None, 'already_pushed'))

def push(*, now: datetime, force: bool, summary: dict[str, Any] | None = None, notify_cfg, send, client=None,
         retro_counts: dict[str, Any] | None = None, fetch_status: list[dict[str, Any]] | None = None,
         features: dict[str, bool] | None = None) -> dict[str, Any]:
    """Send the day's digest; scheduled runs skip when today already pushed successfully.

    ``retro_counts`` (v2.8 D3: ``{'new_cards', 'to_review'}``, see ``retro_push_counts``) feeds the overview's retro-card
    line; ``fetch_status`` (the collect node's family list) the one line for families that fetched partially or failed.
    Both None (the manual push) keep the digest without those lines — except that a ``summary['retro']`` (the v2.7
    ``run_batch`` shell sets one before the push) still feeds the retro line. ``features`` is the run's frozen switch
    set: the 「🧩 组合检查」 message follows the run's ``portfolio_manager`` (None: the current switch)."""
    session = bars.latest_closed_session(now)
    with core_tx._tx() as conn:
        if not force:
            last = workflow_repo.last_push(conn, session=session)
            if last and last.get('sent') is True:
                return {'session': session.isoformat(), 'sent': False, 'reason': 'already_pushed', 'messages': 0}
        view = unit_cards(now=now)
        plan_updates = None
        reviews_until = None
        units, holding_rows, merged = view['units'], None, set()
        if core_schema.timeline_ready(conn):
            # D25: reviews written since the last successful daily push of an earlier session
            # (batch and recheck alike); never pushed before → this session's reviews only. The window
            # ends at this read (clock_timestamp, not the transaction start); it goes into the push result
            # (summary.push.reviews_until) so the next push starts exactly here, not at the report time.
            reviews_until = conn.execute('SELECT clock_timestamp()').fetchone()[0]
            since = workflow_repo.last_successful_push_at(conn, before_session=session)
            plan_updates = _plan_update_rows(conn, execution_repo.reviews_since(conn, since, session,
                                                                             until=reviews_until))
        if core_schema.timeline_ready(conn) and core_schema.executable_ready(conn):
            # v2.9 §11: the new plans are the entry-phase chains and the day's rule baselines (there are no
            # undecided units any more); the holdings are the day's status rows, quiet days included
            units, merged = _push_units(conn, session, view['units'], now=now, plan_updates=plan_updates)
            holding_rows = execution_repo.holding_status_for(conn, session=session)
        portfolio = view.get('portfolio_check')
        if features is not None:          # a switch flipped during the run does not change what this run pushes
            portfolio = portfolio_check_view(conn, session, enabled=feature_enabled({'features': features}, PORTFOLIO_FLAG))
        payload = digest_module.build(
            session=session, units=units, holdings=view.get('holdings', []), summary=summary or {},
            last_push_failed=_last_push_failed(conn, session),
            public_url=load_config().workbench.public_url,
            extended_plans=sum(1 for u in view.get('extended_units', []) if u.get('plan') is not None),
            market=view.get('market'), agent_summary=(summary or {}).get('agent'),
            screen=view.get('screen'), plan_updates=plan_updates,
            retro=retro_counts if retro_counts is not None else (summary or {}).get('retro'),
            fetch_status=fetch_status, holding_status=holding_rows, merged=merged, session_units=view['units'],
            portfolio=portfolio)
    out = send('digest', payload, notify_cfg, client=client)
    result = {'session': session.isoformat(), **out}
    if reviews_until is not None:
        result['reviews_until'] = reviews_until.isoformat()
    return result

def _push_units(conn, session: date, view_units: list[dict[str, Any]], *, now: datetime,
                plan_updates: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], set[tuple]]:
    """The unit entries of the push's 「新计划」 section (v2.9 §11) and the set of today's units merged into a chain.

    One entry per main-list chain in the entry phase at S (judged by `timeline-2`): its head version with the chain
    key and, when a retrigger revision of the window produced that head, the 「再次触发，第 n−1 → n 版」 mark; the
    unit's rule baseline follows, then the baselines of the units merged into the chain today. Today's buy units that
    opened no chain (an Agent pass, error or skip) keep their old entry (rule baseline plus the Agent's reason).
    Extended-pool chains are not pushed."""
    revised = {u['ticker']: u for u in plan_updates or []
               if u.get('via') == 'retrigger' and u.get('outcome') == 'revise' and u.get('to_version') is not None}
    entries: list[dict[str, Any]] = []
    by_chain: dict[tuple, dict[str, Any]] = {}
    for chain in execution_repo.all_chains(conn):
        if chain['pool'] == 'extended':
            continue
        ticker, signal_date = chain['ticker'], chain['signal_date']
        status = execution_chains.chain_status(conn, ticker, signal_date, session=session, now=now)
        if status is None or status['state']['state'] != 'entry':
            continue
        head = status['head']
        run = data_repo.agent_run_by_id(conn, head['version']['agent_run_id'])
        update = revised.get(ticker)
        retrigger = ({'from_version': update['from_version'], 'to_version': update['to_version']}
                     if update and update['to_version'] == head['version']['version_no'] else None)
        entry = data_repo.pool_entry_at(conn, ticker, signal_date, cutoff_at=now)
        unit = {'ticker': ticker, 'signal_date': signal_date, 'proposed_action': 'buy', 'labels': [],
                'rule_ids': [c['rule_id'] for c in data_repo.unit_candidates(conn, ticker, signal_date)],
                'source': entry.get('source') if entry else None,
                'plan': execution_repo.rule_plan(conn, ticker, signal_date),
                'brief': _brief_view(analysis_repo.latest_brief(conn, ticker, signal_date)),
                'agent': _agent_view(run, head['plan'], {**execution_chains._chain_view(status), 'signal_date': signal_date,
                                                         'retrigger': retrigger}),
                'merged': []}
        entries.append(unit)
        by_chain[(ticker, signal_date)] = unit
    merged: set[tuple] = set()
    for u in view_units:
        if u['signal_date'] != session or u.get('proposed_action') != 'buy' or 'conflict' in (u.get('labels') or []):
            continue
        row = execution_repo.retrigger_for_unit(conn, u['ticker'], u['signal_date'])
        if row is not None:
            merged.add((u['ticker'], u['signal_date']))
            target = by_chain.get((u['ticker'], row['chain_signal_date']))
            if target is not None:
                target['merged'].append({'signal_date': u['signal_date'], 'plan': u.get('plan')})
        elif (u['ticker'], u['signal_date']) not in by_chain:
            entries.append({**u, 'merged': []})
    entries.sort(key=lambda e: (e['ticker'], e['signal_date']))
    return entries, merged

def _plan_update_rows(conn, reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The window's invalidations and revisions with a new version, as digest.build reads them: each carries
    its chain's pool (version 0's run) and version numbers, a revision also its `changes` texts and the new
    version's levels. Other rows (maintain, failed revisions, skips, errors) are dropped before any lookup."""
    pools: dict[tuple, str | None] = {}
    out = []
    for row in reviews:
        if not (row['outcome'] == 'invalidate' or row['new_plan_id'] is not None):
            continue
        item = {k: row[k] for k in ('ticker', 'signal_date', 'session', 'trigger', 'mode', 'outcome',
                                    'decided_by', 'reason', 'new_plan_id', 'created_at')}
        key = (row['ticker'], row['signal_date'])
        if key not in pools:
            first = next(iter(execution_repo.chain_versions(conn, *key)), None)
            run = data_repo.agent_run_by_id(conn, first['agent_run_id']) if first else None
            pools[key] = run.get('pool') if run else None
        reviewed = execution_repo.version_by_plan(conn, row['head_plan_id'])
        detail = row.get('detail') or {}
        item.update(pool=pools[key], from_version=reviewed['version_no'] if reviewed else None,
                    to_version=None, changes=[], levels=None, valid_until=None, via=detail.get('via'),
                    exit_reason=detail.get('exit_reason'), retrigger=detail.get('retrigger'))
        if row['outcome'] == 'revise' and row['new_plan_id'] is not None:
            version = execution_repo.version_by_plan(conn, row['new_plan_id'])
            plan = execution_repo.plan_by_id(conn, row['new_plan_id']) or {}
            run = (data_repo.agent_run_by_id(conn, row['revision_agent_run_id'])
                   if row.get('revision_agent_run_id') else None)
            changes = ((run or {}).get('output') or {}).get('changes') or []
            item.update(to_version=version['version_no'] if version else None,
                        changes=[c['text'] for c in changes if isinstance(c, dict) and c.get('text')],
                        levels={k: plan.get(k) for k in ('entry_low', 'entry_high', 'stop', 'target1')},
                        valid_until=plan.get('valid_until'))
        out.append(item)
    return out

def push_closed(*, now: datetime, notify_cfg, send, client=None) -> dict[str, Any]:
    """One line for a New York holiday instead of the digest."""
    ny_date = now.astimezone(NY).date()
    out = send('closed', ny_date, notify_cfg, client=client)
    return {'session': ny_date.isoformat(), **out}
