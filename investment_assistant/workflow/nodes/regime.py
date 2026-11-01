from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from investment_assistant.core import stages as stages_module, tx as core_tx
from investment_assistant.core.constants import BENCHMARK
from investment_assistant.core.tx import Unavailable
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import fred as macro_module
from investment_assistant.analysis import pool as pool_module, regime as regime_module, repo as analysis_repo, units as analysis_units
from investment_assistant.workflow.state import _moment, _pool_from_state

ETF_TICKERS = pool_module.SECTOR_ETFS

def run(state: dict[str, Any], ctx, acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``regime`` node. Market context sits between collection and rules; pool bars exclude off-pool holdings.

    An exception is booked in ``summary`` (as in v2.7) and then raised: the node retries it and, failing again, stops
    the chain — the one stage that no longer carries on after a failure, because ``signals`` depend on today's
    regime (v2.8 §5.2, §22 Q5). The v2.7 ``run_batch`` shell catches it and carries on."""
    clock = ctx.clock
    now, session = _moment(state)
    summary = state['summary']
    manual_pool = _pool_from_state(state.get('manual_pool') or state['pool'])
    t_regime = clock()
    try:
        with core_tx._tx() as conn:
            if ctx.breadth_tickers is not None:
                breadth_sample = list(ctx.breadth_tickers)
            elif not state.get('explicit'):
                loaded = state.get('universe')
                breadth_sample, breadth_notes = analysis_units._breadth_tickers(
                    manual_pool, SimpleNamespace(tickers=tuple(loaded['tickers'])) if loaded else None)
                summary['errors'].extend(breadth_notes)
            else:
                breadth_sample = list(manual_pool.tickers)
            pool_bars = {t: b for t in breadth_sample
                         if (b := data_repo.read_bars(conn, t, session, cutoff_at=now, limit=300))}
            etf_read = {etf: data_repo.read_bars(conn, etf, session, cutoff_at=now) for etf in ETF_TICKERS}
            spy_read = data_repo.read_bars(conn, BENCHMARK, session, cutoff_at=now)
            macro_read = {s: data_repo.read_macro(conn, s, end_date=session, cutoff_at=now)
                          for s in macro_module.MACRO_SERIES}
            macro_status = {s: v if v == 'not_configured' else
                            ('error' if isinstance(v, str) and v.startswith('error:') else 'ok')
                            for s, v in summary['macro'].items()}
            snap = regime_module.snapshot(session=session, pool_bars=pool_bars, etf_bars=etf_read,
                                          spy_bars=spy_read, macro_rows=macro_read, macro_status=macro_status)
            out = analysis_repo.store_regime(conn, snap, now=now)
        summary['regime'] = {'status': out['status'], 'label': snap['label']}
        if out['status'] == 'conflict':
            summary['conflicts'].append({'ticker': None, 'rule_id': 'market_regime',
                                         'id': str(out['record']['id'])})
    except Unavailable:
        raise
    except Exception as exc:
        summary['regime'] = f'error: {exc}'
        summary['sources']['regime'] = ['all']
        summary['errors'].append(f'regime: {exc}')
        summary['timings']['regime'] = round(clock() - t_regime, 1)
        raise
    if acc is not None:
        acc.input(len(breadth_sample))
        acc.output(1)      # 写入或已存的快照（§4）
        acc.set_ref('market_regime_id', out['record']['id'])
        if snap['label'] == 'insufficient':
            acc.degrade('label_insufficient', '')      # 广度样本不足
        for series_id, state_ in macro_status.items():
            if state_ != 'ok':
                acc.degrade('macro_not_ok', f'{series_id}:{state_}')
        if out['status'] == 'conflict':
            acc.degrade('conflict', '')
        acc.duration_ms += (clock() - t_regime) * 1000
    summary['timings']['regime'] = round(clock() - t_regime, 1)
    return {'summary': summary}
