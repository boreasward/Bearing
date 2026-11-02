from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime
from typing import Any

from investment_assistant.config import AssistantConfig
from investment_assistant.core import features as core_features, schema as core_schema, stages as stages_module, tx as core_tx
from investment_assistant.data import collect
from investment_assistant.analysis import pool as pool_module, screening, units as analysis_units
from investment_assistant.workflow import screen_collection as workflow_screen_collection
from investment_assistant.workflow.state import _moment, _pool_from_state, _pool_state, _stage_skip, _stage_finish, _wall
from investment_assistant.workflow.nodes.collect import _note_sec_errors

def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``screen`` node: ``screen-1`` over what ``collect`` stored (no requests for the range), members into the
    extended pool; the members the collect node did not cover get their news and SEC listings now (v2.8 §4.5)."""
    deps, clock = ctx.deps, ctx.clock
    now, session = _moment(state)
    run_id, record_trigger, job = state.get('run_id'), ctx.record_trigger, ctx.job
    summary = state['summary']
    pool = manual_pool = _pool_from_state(state['pool'])
    held = list(state.get('held') or [])
    if state.get('explicit'):
        summary['screen'] = {'status': 'skipped', 'reason': 'explicit_pool'}
        _stage_skip(ctx, 'screen', 'explicit_pool')      # 显式池：整阶段跳过（v2.4 §4）
        return {'summary': summary, 'pool': _pool_state(pool), 'held': held}
    universe_meta = state.get('universe') or {}
    s_started = clock() if acc is not None else 0.0
    stage = screening.run_stage(session=session, now=now, manual=pool, held=held, deps=deps, run_id=run_id,
                                fetched=set(universe_meta.get('fetched') or ()),
                                fetch_reasons=dict(universe_meta.get('fetch_reasons') or {}))
    failed: str | None = None
    if acc is not None:
        acc.duration_ms += (clock() - s_started) * 1000
        acc.input(len(stage['universe'].tickers) if stage['universe'] is not None else 0)
        acc.output(len(stage['summary']['selected']))
        if stage['summary']['screen_run_id'] is not None:
            acc.set_ref('screen_run_id', stage['summary']['screen_run_id'])
        error = stage['summary']['error']
        if error is not None and not (error.startswith('universe:') or error == 'benchmark_missing'):
            failed = error
        elif error is not None:
            # 范围文件加载失败或 QQQ 缺当日数据、沿用成员：降级不是失败（§4）
            acc.degrade('universe_load_failed' if error.startswith('universe:') else 'benchmark_missing', error)
    summary['screen'] = stage['summary']
    if stage['status'] == 'error':
        summary['errors'].append(f"screen: {stage['summary']['error']}")
    pool = pool_module.with_screen(pool, stage['members'])
    with core_tx._tx() as conn:
        tier_changes = analysis_units._sync_pool(conn, pool, session, now)
    # A held screen member is in the pool now; run it once, not again as an off-pool holding.
    held = [t for t in held if t not in pool.tickers]
    summary['pool'] = {**summary['pool'], 'extended': len(pool.extended), 'holdings': len(held),
                       'screen': len(stage['members']), 'changes': tier_changes}

    # v2.8 D4: members that collect did not cover (collect only reads the manual pool, holdings and chains) get
    # their news and SEC listings now; today's range bars and earnings dates are already stored.
    covered = set(state.get('collected') or ())
    new_members = [t for t in stage['members'] if t not in covered]
    if new_members:
        _backfill_members(new_members, state=state, ctx=ctx, acc=acc, summary=summary, now=now, session=session,
                          fetched=set(universe_meta.get('fetched') or ()), job=job,
                          record_trigger=record_trigger, run_id=run_id)
    if failed is not None:
        _stage_finish(ctx, 'screen', failed=failed)
    return {'summary': summary, 'pool': _pool_state(pool), 'manual_pool': _pool_state(manual_pool), 'held': held}

def _run_features(state: dict[str, Any]) -> dict[str, bool]:
    """The run's frozen switches; the v2.7 shell has none, so the current ones (default on) — as the collect node reads them."""
    frozen = state.get('features')
    if frozen is not None:
        return dict(frozen)
    with core_tx._tx() as conn:
        return core_features.frozen(conn) if core_schema.five_layers_ready(conn) else dict.fromkeys(core_features.FLAGS, True)

def _backfill_members(new_members: list[str], *, state: dict[str, Any], ctx, acc, summary: dict[str, Any],
                      now: datetime, session: date, fetched: set[str], job: str,
                      record_trigger: str | None, run_id: str | None) -> None:
    """News and SEC listing for the screen's new members; failures only count against the screen node (``partial``)."""
    drop_after = now if ctx.resumed else None
    # The v2.7 shell never wrote rows for these members (it collected them with the pool): no tallies, locks or rows.
    tallies = ({'news': collect.Tally('news', job, new_members),
                'sec_filings': collect.Tally('sec_filings', job, new_members)}
               if (record_trigger is not None or acc is not None) and not ctx.legacy else None)
    wall = _wall(ctx)
    started_at = wall() if tallies is not None else None
    with ExitStack() as locks:
        if record_trigger is not None and not ctx.legacy:
            locks.enter_context(collect.family_locks(['news', 'sec_filings']))
        got = workflow_screen_collection.backfill_new_members(
            new_members, session=session, now=now, deps=ctx.deps, drop_after=drop_after, fetched=fetched,
            ny_date=date.fromisoformat(state['ny_date']), features=_run_features(state),
            config=ctx.config if isinstance(ctx.config, AssistantConfig) else None,
            trigger=record_trigger or 'scheduled', run_id=run_id,
            news_tally=tallies['news'] if tallies is not None else None,
            sec_tally=tallies['sec_filings'] if tallies is not None else None)
    for ticker in new_members:
        problems = list(got['problems'].get(ticker) or [])
        bars_ = got['bars'].get(ticker)
        entry = {'bars': bars_['counts'] if isinstance(bars_, dict) else bars_,
                 'skipped_bars': [d.isoformat() for d in bars_['skipped']] if isinstance(bars_, dict) else [],
                 'news': got['news'].get(ticker), 'candidates': [], 'skips': [], 'error': None}
        if any(p.startswith('bars:') for p in problems):
            summary['sources']['bars'].append(ticker)
        if any(p.startswith('news:') for p in problems):
            summary['sources']['news'].append(ticker)
        status = got['earnings'].get(ticker)
        if status is not None:
            summary['earnings'][ticker] = status
            if status.startswith('error:'):
                summary['errors'].append(f'{ticker} earnings: {status[len("error: "):]}')
                summary['sources']['earnings'].append(ticker)
        if problems:
            entry['error'] = '; '.join(problems)
            summary['errors'].extend(f'{ticker} {p}' for p in problems)
        summary['tickers'][ticker] = entry
    filings_error, listed = got['filings_error'], got['filings']
    if filings_error is None and listed is not None and listed.get('errors') and not listed.get('by_ticker'):
        filings_error, listed = next(iter(listed['errors'].values())), None     # nothing listed: the v2.7 whole-call failure
    if filings_error is not None:
        if 'error' not in (summary.get('filings') or {}):       # one failure line, however many calls failed
            summary['errors'].append(f"SEC listings: {filings_error}")
        summary['sources']['sec'] = ['all']
        if not isinstance(summary.get('filings'), dict) or 'error' not in summary['filings']:
            summary['filings'] = {'error': filings_error}
    elif listed is not None:
        _merge_filings(summary, listed)
        if 'error' not in (summary.get('filings') or {}):
            _note_sec_errors(summary, listed, whole_call=False)
    if tallies is not None:
        finished_at = wall()
        rows = [tallies[family].row(trigger=record_trigger or 'scheduled', run_id=run_id, session=session,
                                    started_at=started_at, finished_at=finished_at)
                for family in ('news', 'sec_filings')]
        for row in rows:
            row['detail']['scope'] = 'screen_new_members'
        if record_trigger is not None:
            _, fetch_ids = collect.write_runs_with_ids(rows)
            if acc is not None and fetch_ids:
                acc.set_ref('data_fetch_runs', fetch_ids)
        if acc is not None:
            failed_targets = set()
            for row in rows:
                for failure in row['failures']:
                    acc.fail(f"{'news' if row['family'] == 'news' else 'sec'}:{failure['target']}", failure['reason'])
                    failed_targets.add(failure['target'])
            for ticker in new_members:
                if any(p.startswith('bars:') for p in got['problems'].get(ticker) or []):
                    acc.fail(f'bars:{ticker}', next(p for p in got['problems'][ticker] if p.startswith('bars:')))
            failed = len(failed_targets - {'*'})
            acc.detail['new_members'] = list(new_members)
            acc.detail['backfill'] = {'targets': len(new_members), 'ok': max(0, len(new_members) - failed),
                                      'failed': failed}

def _merge_filings(summary: dict[str, Any], listed: dict[str, Any]) -> None:
    """Add a second listing (the screen's new members) to ``summary['filings']``: counts add up, per-ticker maps merge."""
    base = summary.get('filings')
    if not isinstance(base, dict) or 'error' in base:
        summary['filings'] = listed
        return
    for key in ('inserted', 'existing', 'acceptance_filled', 'after_cutoff_dropped'):
        if key in listed:
            base[key] = int(base.get(key) or 0) + int(listed[key] or 0)
    base.setdefault('by_ticker', {}).update(listed.get('by_ticker') or {})
    base.setdefault('errors', {}).update(listed.get('errors') or {})
