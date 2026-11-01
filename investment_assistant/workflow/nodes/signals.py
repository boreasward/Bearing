from __future__ import annotations

import copy
from contextlib import nullcontext
from typing import Any

from investment_assistant.core import schema as core_schema, stages as stages_module, tx as core_tx
from investment_assistant.core.tx import Unavailable
from investment_assistant.data import repo as data_repo
from investment_assistant.execution import chains as execution_chains, plan_context as execution_plan_context
from investment_assistant.execution import repo as execution_repo, repo_legacy as execution_repo_legacy, timeline
from investment_assistant.analysis import repo as analysis_repo, rules, synthesis, units as analysis_units
from investment_assistant.workflow.state import _moment, _pool_from_state


def run(state, ctx, acc):
    return stage_rules(state, ctx, acc, stages_module.StageAcc(ctx.clock))

def _note_signals(acc: stages_module.StageAcc, ticker: str, noted: dict[str, Any]) -> None:
    """Fold one committed ticker's candidates, rule skips and unit action into the signals stage."""
    for rule_id, out in noted['candidates']:
        if out['status'] == 'conflict':
            acc.degrade('conflict', f'{ticker}:{rule_id}')
            continue
        acc.output()
        acc.ref('inserted' if out['status'] == 'inserted' else 'existing', out['id'])
    for reason in noted['skips']:
        acc.skip(reason, ticker)
    if 'action' in noted:
        units = acc.detail.setdefault('units', {})
        units[noted['action']] = units.get(noted['action'], 0) + 1

def _note_plan(acc: stages_module.StageAcc, ticker: str, planned: dict[str, Any] | None,
               summary_text: str) -> None:
    """Fold one rule-plan attempt into the plan stage: a skip keeps its reason, a stored plan its id."""
    if planned is None:
        acc.skip(summary_text.removeprefix('skip: '), ticker)
        return
    acc.output()
    acc.ref('inserted' if planned['status'] == 'inserted' else 'existing', planned['id'])
    if planned.get('plan_status') in ('ok', 'invalid'):      # detail.status 与 outputs 里的计划一一对应
        counts = acc.detail.setdefault('status', {'ok': 0, 'invalid': 0})
        counts[planned['plan_status']] += 1
    if planned['status'] == 'conflict':
        acc.degrade('conflict', ticker)

def stage_rules(state: dict[str, Any], ctx, sig_acc: stages_module.StageAcc | None,
                plan_acc: stages_module.StageAcc | None) -> dict[str, Any]:
    """The ``signals`` node's body: the rule loop over the pool and the holdings, one transaction per ticker, which
    also writes the rule plan of a fresh buy unit (v2.8 §4.3, §22 Q4). The ``plan`` accumulator is frozen into
    ``refs['plan_acc']``: the ``plan`` node only re-derives and writes its own row."""
    clock = ctx.clock
    now, session = _moment(state)
    summary, run_id = state['summary'], state.get('run_id')
    all_tickers = [*_pool_from_state(state['pool']).tickers, *(state.get('held') or [])]
    t_rules = clock()
    for acc_ in (sig_acc, plan_acc):   # 空跑与重跑时两个列表也在（inserted 为 []，v2.4 §4）
        if acc_ is not None:
            acc_.outputs.update({'inserted': [], 'existing': []})
    if plan_acc is not None:           # v2.9 §4.2: every run states how many units merged into an active chain
        plan_acc.detail['retriggers'] = analysis_units._empty_retriggers()
    for ticker in all_tickers:
        entry = summary['tickers'].setdefault(ticker, {'bars': None, 'skipped_bars': [], 'news': None,
                                                       'candidates': [], 'skips': [], 'error': None})
        if sig_acc is not None:
            sig_acc.input()
        phase = 'signals'          # 异常归到出错的那个阶段
        noted: dict[str, Any] = {}  # 本只的记账，事务提交后才并入阶段（回滚的写入不进证据）
        try:
            with core_tx._tx() as conn:
                ticker_bars = data_repo.read_bars(conn, ticker, session, cutoff_at=now)
                events, _ = execution_plan_context._filing_events(conn, ticker, session, now)
                book = execution_plan_context._book(conn, ticker)
                _, plan_state = execution_chains._position_plan_state(conn, ticker, book, ticker_bars, session, now=now)
                with (sig_acc.timing() if sig_acc is not None else nullcontext()):
                    result = rules.evaluate(ticker, session, ticker_bars, events, book['position'],
                                            plan_state=plan_state)
                    stored_candidates = []
                    for cand in result['candidates']:
                        out = analysis_repo.store_candidate(conn, ticker, session, cand, run_id=run_id)
                        stored_candidates.append((cand.rule_id, out))
                        if out['status'] == 'conflict':
                            summary['conflicts'].append({'ticker': ticker, 'rule_id': cand.rule_id, 'id': str(out['id'])})
                entry['candidates'] = [c.rule_id for c in result['candidates']]
                entry['skips'] = sorted({f'{s.rule_id}:{s.reason}' for s in result['skips']})
                noted['candidates'] = stored_candidates
                noted['skips'] = entry['skips']
                group = data_repo.unit_candidates(conn, ticker, session)
                unit = synthesis.synthesize(ticker, session, group, holding=book['position'] is not None,
                                            now=now) \
                    if group and execution_repo_legacy.decision_head(conn, ticker, session) is None else None
                if unit is not None:
                    noted['action'] = unit['proposed_action']
                if unit is not None and unit['proposed_action'] == 'buy' and 'conflict' not in unit['labels']:
                    phase = 'plan'
                    if plan_acc is not None:
                        plan_acc.input()
                    with (plan_acc.timing() if plan_acc is not None else nullcontext()):
                        # v2.9 §4.2 (R2): the active-chain decision and the chain_retriggers row share this
                        # ticker's transaction with the rule baseline, under the chain-open lock
                        merged = (analysis_units._open_or_merge(conn, ticker, session, now=now)
                                  if core_schema.timeline_ready(conn) and core_schema.executable_ready(conn) else None)
                        found = (merged or {}).get('found')
                        stored = (merged or {}).get('row')
                        target = ({'ticker': ticker, 'signal_date': found['chain']['signal_date']} if found
                                  else {'ticker': ticker, 'signal_date': stored['chain_signal_date']} if stored
                                  else None)
                        noted['plan'] = execution_plan_context._generate_rule_plan(conn, ticker, session, ticker_bars, summary, now=now,
                                                            active_chain=target)
                        if found is not None:
                            noted['retrigger'] = execution_repo.insert_chain_retrigger(conn, {
                                'ticker': ticker, 'unit_signal_date': session,
                                'chain_signal_date': found['chain']['signal_date'],
                                'chain_state': found['chain_state'], 'rule_ids': unit['rule_ids'],
                                'baseline_plan_id': noted['plan']['id'] if noted['plan'] else None,
                                'entry_cap': timeline.retrigger_cap(found['chain_state'], session),
                                'run_id': run_id or 'unknown', 'timeline_version': timeline.TIMELINE_VERSION,
                                'created_at': now})
                        elif stored is not None:
                            noted['retrigger'] = stored
                    noted['planned'] = True
                # Briefs come from the agent stage only (P4 §7.3): propose for
                # conflict-free buys, interpret for everything else on the main list.
        except Unavailable:
            raise
        except Exception as exc:
            entry['error'] = '; '.join(filter(None, [entry['error'], f'rules: {exc}']))
            summary['errors'].append(f'{ticker} rules: {exc}')
            if sig_acc is not None:
                (plan_acc if phase == 'plan' else sig_acc).fail(ticker, exc)
        else:
            if sig_acc is not None:
                _note_signals(sig_acc, ticker, noted)
                if noted.get('planned'):
                    _note_plan(plan_acc, ticker, noted['plan'], summary['plans'][ticker])
                    if noted.get('retrigger') is not None:
                        analysis_units._note_retrigger(plan_acc, ticker, noted['retrigger'])
    summary['timings']['rules'] = round(clock() - t_rules, 1)
    delta: dict[str, Any] = {'summary': summary}
    if plan_acc is not None:
        frozen = copy.deepcopy(plan_acc.freeze())
        # What the plan stage did inside the signals loop: its own timing, not the row's measured time.
        frozen['detail'] = {**frozen['detail'], 'timings': {'in_signals_ms': round(frozen['duration_ms'])}}
        frozen['duration_ms'] = 0.0
        delta['refs'] = {**(state.get('refs') or {}), 'plan_acc': frozen}
    return delta
