"""Plan timeline `timeline-1` (v2.5 spec §5, §7.3, §7.4, §8.1) and `timeline-2` (v2.9 spec §4, §7). Pure functions.

Chain state, version validity and plan rows, the "since last review" window,
system signals, condition results, host verdicts and effective user inputs.
Prompt evidence is assembled in analysis.agent.evidence. Nothing here reads the database or calls a model;
trading-day arithmetic only goes through data.calendar.add_trading_days.

Readings decided here (see the task-4 report for the full list):
- chain_state takes the unit's buys and the ticker's open lots and filters
  them itself: only live (unvoided) buys count, and only lots opened by those
  buys, so callers may pass positions.build(...)['open'] for the whole ticker.
- Several met invalidation conditions (or several review conditions in the
  maintain reason) are joined with '；' in list order.
- regime_change treats a missing label on either side, and 'insufficient',
  as unknown (fired False, detail 'unknown'), like condition-1's regime_is.
- new_8k is unknown (fired False, detail 'unknown') when filings is None.
- New user inputs in the prompt: the newest ones first until 10 items or 8000
  characters of body would be exceeded, then shown oldest first.

`timeline-2` (v2.9) lives beside `timeline-1`, not over it: `chain_state` (v1 shape and rules) is untouched and
`chain_state_v2` is the nine-state judgement; `signals` gets the eighth `retrigger` signal only when a caller
passes `retriggers=`; `version_validity` / `build_version_plan` take the new behaviour through new keyword
arguments with the old defaults. Callers are switched to the new functions in Task 3 / 4.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from investment_assistant.config import WorkbenchConfig
from investment_assistant.core.versions import (AGENT_V2 as AGENT_V2, AGENT_V3 as AGENT_V3,
                                                TIMELINE_V1 as TIMELINE_V1, TIMELINE_V2 as TIMELINE_V2)
from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.execution import entry_rules
from investment_assistant.execution import exits
from investment_assistant.execution import plan_levels as plans
from investment_assistant.execution import conditions as condition_registry
from investment_assistant.execution import plan_check
from investment_assistant.derivation.indicators.price import atr14

# v2.9: plan_review judges chains with `chain_state_v2` and stamps its rows `timeline-2` (a holding-mode
# invalidation of that version is the exit phase, D17).
TIMELINE_VERSION = TIMELINE_V2
CHAIN_ENTRY_SESSIONS = 10
ENDED_WINDOW_SESSIONS = 5            # the 「今日」 ended group lists chains / plans that ended within 5 sessions

# SIGNAL_KINDS is timeline-1's seven (plan_review counts and writes exactly these until Task 3 passes
# retriggers=); SIGNAL_KINDS_V2 is the timeline-2 list with `retrigger` as the eighth.
SIGNAL_KINDS = ('new_8k', 'abnormal_move', 'stop_touched', 't1_touched', 'expiry_near', 'regime_change',
                'user_input')
SIGNAL_KINDS_V2 = (*SIGNAL_KINDS, 'retrigger')
CHAIN_STATES = ('legacy', 'invalidated', 'closed_by_decision', 'exiting', 'holding', 'position_closed',
                'superseded', 'expired', 'entry')
REVIEWABLE_STATES = ('entry', 'holding')                     # reviewed daily, may be rechecked (R10)
ACTIVE_STATES = ('entry', 'holding', 'exiting')              # at most one per ticker; retriggers merge into it
ENDED_STATES = ('invalidated', 'closed_by_decision', 'position_closed', 'superseded', 'expired')
MODES = ('entry', 'holding')
VALID_OUTCOMES = ('maintain', 'revise', 'invalidate')     # an "effective" review (spec §6.2)

NEW_INPUTS_MAX = 10
NEW_INPUTS_CHARS = 8000

LEGACY_TEXT = 'agent-1（旧版，不参与复查）'
REASON_JOIN = '；'

diff = plan_check.diff                # the one host diff (spec §4.1 names it timeline.diff)


# ---------------------------------------------------------------- helpers

def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and str(a) == str(b)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _plain(value: Any) -> Any:
    """JSON-safe copy: Decimal -> str, date / datetime -> ISO string, UUID -> str."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _money(value: Any) -> str:
    try:
        return f'{_dec(value):.2f}'
    except Exception:   # noqa: BLE001 - display only
        return str(value)


# ---------------------------------------------------------------- chain state

def _head_version(versions: list[dict]) -> dict:
    return max(versions, key=lambda v: v['version_no'])


def chain_state(*, versions: list[dict], head_plan: dict | None, reviews: list[dict], decision: dict | None,
                unit_buys: list[dict], open_lots: list[dict], session: date, legacy: bool) -> dict:
    """{'state', 'mode', 'basis'} at review session S; the first matching row of spec §5.3 wins.

    versions: the chain's agent_plan_versions rows; head_plan: the trade_plans row
    of the highest version_no; reviews: the chain's plan_reviews rows; decision:
    the unit's head decision (None = undecided); unit_buys: trades on the unit's
    decision chain; open_lots: open FIFO lots (any lot not opened by a live unit
    buy is ignored).
    """
    if legacy:
        return {'state': 'legacy', 'mode': None, 'basis': LEGACY_TEXT}
    if not versions or head_plan is None:
        raise ValueError('chain_state needs the agent-2 versions and the head plan')
    head = _head_version(versions)
    if not _same(head['plan_id'], head_plan.get('id')):
        raise ValueError('head_plan is not the highest version of the chain')
    version_no = head['version_no']

    killed = [r for r in reviews if r.get('outcome') == 'invalidate'
              and (r.get('head_plan_id') is None or _same(r.get('head_plan_id'), head_plan['id']))]
    if killed:
        when = killed[-1].get('session')
        suffix = f'（{_iso(when)}）' if when is not None else ''
        return {'state': 'invalidated', 'mode': None, 'basis': f'第 {version_no} 版已被复查作废{suffix}'}

    if decision is not None and decision.get('final_action') != 'buy':
        return {'state': 'closed_by_decision', 'mode': None,
                'basis': f"最新决定为 {decision.get('final_action')}，不买"}

    buy_ids = {str(b['id']) for b in unit_buys
               if b.get('voided_at') is None and b.get('side', 'buy') == 'buy'}
    lots = [lot for lot in open_lots
            if str(lot.get('buy_trade_id')) in buy_ids and _dec(lot.get('quantity', 0)) > 0]
    if lots:
        return {'state': 'holding', 'mode': 'holding',
                'basis': f'决定链上的买入成交还有 {len(lots)} 个未平批次'}
    if buy_ids:
        return {'state': 'position_closed', 'mode': None, 'basis': f'决定链上的 {len(buy_ids)} 笔买入已全部平仓'}

    valid_until = head_plan['valid_until']
    if session >= valid_until:
        return {'state': 'expired', 'mode': None,
                'basis': f'{session.isoformat()} 已到第 {version_no} 版截止日 {valid_until.isoformat()}，未成交'}
    status = '未决定' if decision is None else '已决定买入，尚未成交'
    return {'state': 'entry', 'mode': 'entry',
            'basis': f'{status}；第 {version_no} 版有效至 {valid_until.isoformat()}'}


# ---------------------------------------------------------------- chain state, timeline-2

NY = ZoneInfo('America/New_York')


def _state(state: str, mode: str | None, basis: str, basis_ref: dict | None = None, *,
           renewable: bool = False) -> dict:
    return {'state': state, 'mode': mode, 'active': state in ACTIVE_STATES, 'basis': basis,
            'basis_ref': basis_ref or {}, 'timeline_version': TIMELINE_V2, 'renewable': renewable}


def _at_or_before(row: dict, now: datetime, *keys: str) -> bool:
    """True when every listed timestamp the row carries is at or before the cutoff."""
    return all(row.get(key) is None or row[key] <= now for key in keys)


def _live_buy_at(trade: dict, now: datetime) -> bool:
    """A buy the cutoff knew of: executed and recorded by `now`, and not voided by `now` (a void after the
    cutoff had not happened yet)."""
    if trade.get('side', 'buy') != 'buy' or not _at_or_before(trade, now, 'executed_at', 'recorded_at'):
        return False
    return trade.get('voided_at') is None or trade['voided_at'] > now


def _ny_day(ts: Any) -> date:
    return ts.astimezone(NY).date() if isinstance(ts, datetime) else ts


def entry_cap(chain: dict, retriggers: list[dict]) -> date:
    """The entry-phase cap (D6): the larger of the first-version cap (signal_date + 10 sessions) and every
    retrigger's `entry_cap` (itself the retrigger's unit_signal_date + 10 sessions). `retriggers` are the
    chain's `chain_retriggers` rows the caller already cut at the cutoff; holding-state rows carry no cap."""
    caps = [add_trading_days(chain['signal_date'], CHAIN_ENTRY_SESSIONS)]
    caps += [row['entry_cap'] for row in retriggers if row.get('entry_cap') is not None]
    return max(caps)


def retrigger_cap(chain_state_name: str, unit_signal_date: date) -> date | None:
    """`chain_retriggers.entry_cap` for a trigger on `unit_signal_date`: set only for an entry-phase chain."""
    return add_trading_days(unit_signal_date, CHAIN_ENTRY_SESSIONS) if chain_state_name == 'entry' else None


def _is_exit_review(review: dict) -> bool:
    """A timeline-2 holding-mode invalidation: the exit phase, not the end of the chain (D17)."""
    return review.get('mode') == 'holding' and review.get('timeline_version') == TIMELINE_V2


def _superseded_by(chain: dict, head_plan: dict, retriggers: list[dict], buys: list[dict], later: list[date],
                   session: date) -> date | None:
    """R1: the earliest later chain B whose signal date found this chain still in its entry phase, judged
    at B's signal date alone, so a chain opened after this one expired never rewrites it."""
    for opened in sorted(day for day in later if chain['signal_date'] < day <= session):
        if opened > head_plan['valid_until']:
            continue
        cap_then = entry_cap(chain, [r for r in retriggers if r['unit_signal_date'] <= opened])
        if opened > cap_then:
            continue
        if any(_ny_day(b['executed_at']) <= opened for b in buys):
            continue
        return opened
    return None


def chain_state_v2(chain: dict, *, session: date, now: datetime, facts: dict) -> dict:
    """`timeline-2` chain state at review session S and cutoff `now` (spec §4.1, R1, R5, R10).

    The first matching row wins: legacy, invalidated, closed_by_decision, exiting, holding, position_closed,
    superseded, expired, entry. Returns `{state, mode, active, basis, basis_ref, timeline_version, renewable}`;
    `mode` is 'entry' / 'holding' for the two reviewable states, else None; `renewable` marks an expired chain
    on its last valid day (R5) so a trigger that day renews it instead of opening a second chain.

    chain: `{ticker, signal_date, legacy, versions, head_plan, reviews}` (versions = agent_plan_versions rows,
    head_plan = the trade_plans row of the highest version, reviews = the chain's plan_reviews rows).
    facts: `{decision, buys, open_lots, retriggers, later_chains}`: the unit's head decision (old data only),
    `execution.chain_buys(...)`, the ticker's open lots, the chain's `chain_retriggers` rows, and
    `[{signal_date}]` of the ticker's other chains. Every record is cut at `now` here: reviews and retriggers
    by `created_at`, buys by execution / recording / void time.
    """
    if chain.get('legacy'):
        return _state('legacy', None, LEGACY_TEXT)
    versions, head_plan = chain.get('versions'), chain.get('head_plan')
    if not versions or head_plan is None:
        raise ValueError('chain_state_v2 needs the chain versions and the head plan')
    head = _head_version(versions)
    if not _same(head['plan_id'], head_plan.get('id')):
        raise ValueError('head_plan is not the highest version of the chain')
    version_no = head['version_no']

    reviews = [r for r in chain.get('reviews') or [] if _at_or_before(r, now, 'created_at')
               and (r.get('head_plan_id') is None or _same(r.get('head_plan_id'), head_plan['id']))]
    invalidations = [r for r in reviews if r.get('outcome') == 'invalidate']
    ended = [r for r in invalidations if not _is_exit_review(r)]
    if ended:
        when = ended[-1].get('session')
        suffix = f'（{_iso(when)}）' if when is not None else ''
        return _state('invalidated', None, f'第 {version_no} 版已被复查作废{suffix}',
                      {'review_id': ended[-1].get('id')})

    decision = facts.get('decision')
    if decision is not None and decision.get('final_action') != 'buy':
        return _state('closed_by_decision', None, f"最新决定为 {decision.get('final_action')}，不买")

    buys = [b for b in facts.get('buys') or [] if _live_buy_at(b, now)]
    buy_ids = {str(b['id']) for b in buys}
    lots = [lot for lot in facts.get('open_lots') or []
            if str(lot.get('buy_trade_id')) in buy_ids and _dec(lot.get('quantity', 0)) > 0]
    exit_reviews = [r for r in invalidations if _is_exit_review(r)]
    if lots and exit_reviews:
        first = exit_reviews[0]
        return _state('exiting', None, f"已发出退出指令（{_iso(first.get('session'))}），链上还有 {len(lots)} 个未平批次",
                      {'exit_review_id': first.get('id'), 'exit_session': _iso(first.get('session'))})
    if lots:
        return _state('holding', 'holding', f'链上关联的买入成交还有 {len(lots)} 个未平批次')
    if buy_ids:
        return _state('position_closed', None, f'链上关联的 {len(buy_ids)} 笔买入已全部平仓')
    if exit_reviews:                 # an exit instruction was given, then every fill was voided: stay terminal
        return _state('invalidated', None, f"第 {version_no} 版已发出退出指令（{_iso(exit_reviews[0].get('session'))}），"
                      '链上的成交其后全部作废', {'review_id': exit_reviews[0].get('id')})

    retriggers = [r for r in facts.get('retriggers') or [] if _at_or_before(r, now, 'created_at')]
    later = [c['signal_date'] for c in facts.get('later_chains') or []]
    opened = _superseded_by(chain, head_plan, retriggers, buys, later, session)
    if opened is not None:
        return _state('superseded', None, f'入场期内同标的 {opened.isoformat()} 开了新链，本链被取代',
                      {'superseded_by': opened.isoformat()})

    valid_until = head_plan['valid_until']
    cap = entry_cap(chain, retriggers)
    renewed = any(r['unit_signal_date'] == session for r in retriggers)
    if session >= valid_until or session > cap:
        if not renewed:
            if session > cap:
                basis = f'{session.isoformat()} 已过入场期上限 {cap.isoformat()}，未成交'
            else:
                basis = f'{session.isoformat()} 已到第 {version_no} 版截止日 {valid_until.isoformat()}，未成交'
            return _state('expired', None, basis, renewable=session == valid_until and session <= cap)
        basis = f'今日再次触发，第 {version_no} 版按入场期复查（上限 {cap.isoformat()}）'
    else:
        basis = f'第 {version_no} 版有效至 {valid_until.isoformat()}'
    return _state('entry', 'entry', basis)


def _facts_for(facts_by_chain: dict, chain: dict) -> dict:
    for key in ((chain['ticker'], chain['signal_date']), chain['signal_date'], str(chain['signal_date'])):
        if key in facts_by_chain:
            return facts_by_chain[key]
    return {}


def active_chain(chains: list[dict], *, session: date, now: datetime, facts_by_chain: dict) -> dict | None:
    """The ticker's active chain at S (spec D2, R5), or None.

    `chains` are the ticker's chains (chain_state_v2 input), `facts_by_chain` maps `(ticker, signal_date)` (or the
    signal date alone) to that chain's facts. Active = state entry / holding / exiting, plus an expired chain
    on its last valid day that a trigger may still renew (`renewable`). With several (old data), the one with
    the latest signal date. Returns `{chain, state, chain_state, renewable}`; `chain_state` is the value for
    `chain_retriggers.chain_state` ('entry' for a renewable chain).
    """
    best: dict | None = None
    for chain in chains:
        state = chain_state_v2(chain, session=session, now=now, facts=_facts_for(facts_by_chain, chain))
        renewable = state['renewable'] and state['state'] == 'expired'
        if state['state'] not in ACTIVE_STATES and not renewable:
            continue
        found = {'chain': chain, 'state': state, 'chain_state': 'entry' if renewable else state['state'],
                 'renewable': renewable}
        if best is None or chain['signal_date'] > best['chain']['signal_date']:
            best = found
    return best


# ---------------------------------------------------------------- validity and plan rows

def version_validity(*, signal_date: date, as_of_session: date, version_no: int,
                     mode: str, entry_cap: date | None = None) -> tuple[date, date, date | None]:
    """(valid_from, valid_until, chain_entry_cap) per D4.

    The first version is plans.validity(signal_date). A revision fills from S+1
    through S+3; in the entry phase valid_until is capped at signal_date + 10
    trading days. chain_entry_cap is that cap in the entry phase (first version
    included), None in the holding phase.

    timeline-2 (D6): `entry_cap` replaces the signal_date + 10 default with the cap of the latest trigger
    (see `entry_cap()` below).
    """
    if mode not in MODES:
        raise ValueError(f'unknown chain mode: {mode!r}')
    if mode == 'entry':
        cap = entry_cap if entry_cap is not None else add_trading_days(signal_date, CHAIN_ENTRY_SESSIONS)
    else:
        cap = None
    if version_no == 0:
        valid_from, valid_until = plans.validity(signal_date)
    else:
        valid_from = add_trading_days(as_of_session, 1)
        valid_until = add_trading_days(as_of_session, plans.VALID_SESSIONS)
    if cap is not None:
        valid_until = min(valid_until, cap)
    return valid_from, valid_until, cap


def build_version_plan(*, output: dict, agent_run_id: str, model: str, ticker: str, signal_date: date,
                       as_of_session: date, version_no: int, supersedes_plan_id: str | None, mode: str,
                       bars: list[dict], earnings_row: dict | None, snapshot: dict | None, exposure: dict,
                       params: WorkbenchConfig, context: dict | None, version: str = AGENT_V2,
                       entry_cap: date | None = None) -> dict:
    """The trade_plans row of one agent-2 version (spec §5.2), shaped like plans.build_agent_plan.

    `version=AGENT_V3` (v2.9 spec §5.5) writes `plan_version = 'agent-3'`, the two execution fields from the
    checked output (`entry_type`, `max_hold_sessions`), `inputs.entry_rules_version` and `inputs.chain_mode`
    (R3: the migration lets a holding-mode agent row put the stop above the entry zone). `entry_cap` is the
    entry-phase cap of the latest trigger (D6); without it the cap is signal_date + 10 sessions.

    Levels are the checked output's (already cents); the reference price is the
    as_of_session close (bars after as_of_session are ignored); no ATR freezes 0;
    sizing prices the entry midpoint against this snapshot and exposure, so a
    holding-phase revision may come out no_room / invalid. Earnings are flagged
    over this version's validity, with as_of_session as the reference session.
    """
    levels_raw = output.get('levels') if isinstance(output, dict) else None
    if output.get('action') != 'buy' or not isinstance(levels_raw, dict):
        raise ValueError('only a buy output with levels becomes a plan')
    levels = {key: _dec(levels_raw[key]['value']) for key in plans.LEVEL_KEYS}
    bars = [b for b in bars if b.get('price_date') is None or b['price_date'] <= as_of_session]
    if not bars:
        raise ValueError('no bars through as_of_session')
    if version not in (AGENT_V2, AGENT_V3):
        raise ValueError(f'unknown agent plan version: {version!r}')
    valid_from, valid_until, cap = version_validity(signal_date=signal_date, as_of_session=as_of_session,
                                                    version_no=version_no, mode=mode, entry_cap=entry_cap)
    invalid, warnings = plans.earnings_flags(earnings_row, signal_date=as_of_session, valid_from=valid_from,
                                             valid_until=valid_until)
    raw_atr = atr14(bars)
    atr = plans._freeze(raw_atr) if raw_atr is not None else Decimal(0)
    ref_price = plans._freeze(_dec(bars[-1]['close']))
    entry_mid = (levels['entry_low'] + levels['entry_high']) / 2
    sizing, size_invalid = plans.size(snapshot=snapshot, ref_price=entry_mid, stop=levels['stop'],
                                      entry_high=levels['entry_high'], ticker_value=exposure['ticker_value'],
                                      total_value=exposure['total_value'], params=params)
    invalid_reasons = [*invalid, *size_invalid]
    inputs = plans._freeze_inputs(plans._inputs(bars, earnings_row, snapshot, exposure), context)
    extra_inputs: dict[str, Any] = {}
    exec_fields: dict[str, Any] = {}
    if version == AGENT_V3:
        entry_type, max_hold = output.get('entry_type'), output.get('max_hold_sessions')
        if entry_type not in entry_rules.ENTRY_TYPES or max_hold is None:
            raise ValueError('an agent-3 plan needs the checked entry_type and max_hold_sessions')
        extra_inputs = {'entry_rules_version': entry_rules.ENTRY_RULES_VERSION, 'chain_mode': mode}
        exec_fields = {'entry_type': entry_type, 'max_hold_sessions': max_hold}
    return {
        'ticker': ticker, 'signal_date': signal_date, 'kind': 'agent', 'based_on_id': None,
        'plan_version': version, 'ref_price': ref_price, 'atr': atr, **levels,
        'trail_atr_multiple': plans.TRAIL_ATR_MULTIPLE, 'valid_from': valid_from, 'valid_until': valid_until,
        'sizing': sizing, 'status': 'invalid' if invalid_reasons else 'ok',
        'invalid_reasons': invalid_reasons, 'warnings': [*warnings, *plans.context_flags(context)],
        'inputs': {**inputs, 'agent_run_id': agent_run_id, 'model': model,
                   'as_of_session': as_of_session.isoformat(), 'version_no': version_no,
                   'supersedes_plan_id': str(supersedes_plan_id) if supersedes_plan_id is not None else None,
                   'chain_entry_cap': cap.isoformat() if cap is not None else None, **extra_inputs},
        **exec_fields,
    }


# ---------------------------------------------------------------- since window and signals

def since_window(head_version: dict, last_review: dict | None) -> tuple[date, datetime]:
    """(since_session, since_at) = max of the head version's (as_of_session, cutoff_at) and the
    last effective review's (session, cutoff_at); a skipped / error review does not move it."""
    since_session, since_at = head_version['as_of_session'], head_version['cutoff_at']
    if last_review is not None and last_review.get('outcome', 'maintain') in VALID_OUTCOMES:
        since_session = max(since_session, last_review['session'])
        since_at = max(since_at, last_review['cutoff_at'])
    return since_session, since_at


def _signal(kind: str, fired: bool, detail: str, evidence: list) -> dict:
    return {'kind': kind, 'fired': fired, 'detail': detail, 'evidence': evidence}


def _new_8k(filings: list[dict] | None, since_session: date, since_at: datetime, now: datetime) -> dict:
    if filings is None:
        return _signal('new_8k', False, 'unknown', [])
    hits = []
    for filing in filings:
        if filing.get('form') != '8-K':
            continue
        accepted = filing.get('accepted_at')
        new = (since_at < accepted <= now) if accepted is not None else filing['filing_date'] > since_session
        if new:
            hits.append({'filing_id': _plain(filing.get('id')), 'accession_number': filing.get('accession_number'),
                         'filing_date': _iso(filing.get('filing_date')), 'accepted_at': _iso(accepted)})
    detail = f'新 8-K {len(hits)} 份' if hits else '没有新的 8-K'
    return _signal('new_8k', bool(hits), detail, hits)


def _abnormal(abnormal: list[dict], since_session: date, session: date) -> dict:
    hits = [{'candidate_id': _plain(c.get('id')), 'signal_date': _iso(c['signal_date'])}
            for c in abnormal
            if c.get('rule_id', 'abnormal_move') == 'abnormal_move' and since_session < c['signal_date'] <= session]
    detail = f"异常波动候选 {', '.join(h['signal_date'] for h in hits)}" if hits else '没有异常波动候选'
    return _signal('abnormal_move', bool(hits), detail, hits)


def _touched(kind: str, bars: list[dict], level: Any, since_session: date, session: date) -> dict:
    level = _dec(level)
    window = [b for b in bars if since_session < b['price_date'] <= session]
    if kind == 'stop_touched':
        hits = [{'price_date': _iso(b['price_date']), 'low': _money(b['low'])}
                for b in window if _dec(b['low']) <= level]
        label = f'最低价触及止损 {_money(level)}'
    else:
        hits = [{'price_date': _iso(b['price_date']), 'high': _money(b['high'])}
                for b in window if _dec(b['high']) >= level]
        label = f'最高价触及 T1 {_money(level)}'
    if hits:
        return _signal(kind, True, f"{hits[0]['price_date']} {label}", hits)
    if not window:
        return _signal(kind, False, '窗口内没有日线', [])
    return _signal(kind, False, f'窗口内 {len(window)} 根日线未触及', [])


def _expiry(head_plan: dict, mode: str, session: date) -> dict:
    valid_until = head_plan['valid_until']
    if mode != 'entry':
        return _signal('expiry_near', False, '持仓期不适用', [])
    if add_trading_days(session, 1) == valid_until:
        return _signal('expiry_near', True, f'最新版 {valid_until.isoformat()} 到期',
                       [{'valid_until': valid_until.isoformat()}])
    return _signal('expiry_near', False, f'最新版有效至 {valid_until.isoformat()}', [])


def _regime(head_plan: dict, regime_label: str | None) -> dict:
    context = (head_plan.get('inputs') or {}).get('context') or {}
    before = (context.get('regime') or {}).get('label')
    if before in (None, 'insufficient') or regime_label in (None, 'insufficient'):
        return _signal('regime_change', False, 'unknown', [])
    if before != regime_label:
        return _signal('regime_change', True, f'区制 {before} → {regime_label}',
                       [{'before': before, 'after': regime_label}])
    return _signal('regime_change', False, f'区制仍为 {regime_label}', [])


def _user_input(new_inputs: list[dict], since_at: datetime, now: datetime) -> dict:
    hits = [{'ref': f"user:{row['id']}", 'created_at': _iso(row['created_at'])}
            for row in new_inputs if since_at < row['created_at'] <= now]
    detail = f'新的用户输入 {len(hits)} 条' if hits else '没有新的用户输入'
    return _signal('user_input', bool(hits), detail, hits)


def _retrigger(retriggers: list[dict], since_at: datetime, now: datetime) -> dict:
    """The eighth signal (timeline-2, R4): `chain_retriggers` rows created in (since_at, now], so a trigger
    that lands after a same-day recheck is still new. Rows arrive enriched by the caller with `baseline`
    (the unit's plan-2 levels) and `frozen_values` (the candidates' frozen inputs)."""
    hits = []
    for row in retriggers:
        if not since_at < row['created_at'] <= now:
            continue
        baseline = row.get('baseline') or {}
        hits.append({'unit_signal_date': _iso(row['unit_signal_date']), 'rule_ids': list(row.get('rule_ids') or []),
                     'baseline': {key: _money(baseline[key]) for key in plans.LEVEL_KEYS if key in baseline},
                     'frozen_values': _plain(row.get('frozen_values') or {})})
    if not hits:
        return _signal('retrigger', False, '没有再次触发', [])
    return _signal('retrigger', True, f"再次触发 {len(hits)} 次（{', '.join(h['unit_signal_date'] for h in hits)}）", hits)


def signals(*, head_plan: dict, mode: str, session: date, since_session: date, since_at: datetime,
            now: datetime, bars: list[dict], filings: list[dict] | None, abnormal: list[dict],
            regime_label: str | None, new_inputs: list[dict], retriggers: list[dict] | None = None) -> list[dict]:
    """All system signals in SIGNAL_KINDS order, each {kind, fired, detail, evidence} (spec §7.3).

    Windows: bars and abnormal candidates in (since_session, S]; 8-K filings
    accepted in (since_at, now] (no acceptance time: filing_date > since_session);
    user inputs created in (since_at, now]. filings None = list unavailable.

    timeline-2: passing `retriggers` (the chain's `chain_retriggers` rows, possibly empty) appends the eighth
    `retrigger` signal, windowed by `created_at` in (since_at, now]; without it the seven timeline-1 signals.
    """
    out = [
        _new_8k(filings, since_session, since_at, now),
        _abnormal(abnormal, since_session, session),
        _touched('stop_touched', bars, head_plan['stop'], since_session, session),
        _touched('t1_touched', bars, head_plan['target1'], since_session, session),
        _expiry(head_plan, mode, session),
        _regime(head_plan, regime_label),
        _user_input(new_inputs, since_at, now),
    ]
    if retriggers is not None:
        out.append(_retrigger(retriggers, since_at, now))
    return out


# ---------------------------------------------------------------- conditions and host verdict

def condition_results(*, invalidation: list[dict], review_when: list[dict],
                      invalidation_facts: condition_registry.Facts,
                      review_facts: condition_registry.Facts) -> list[dict]:
    """[{list, index, kind, params, result, detail}] for plan_reviews.conditions (spec §6.2).

    invalidation_facts covers the session after the version's as_of_session
    through S; review_facts covers the session after since_session through S.
    Both must carry the version's as_of_session (conditions.Facts contract).
    """
    out = []
    for name, items, facts in (('invalidation', invalidation, invalidation_facts),
                               ('review_when', review_when, review_facts)):
        for index, item in enumerate(items or []):
            result, detail = condition_registry.evaluate(item, facts)
            out.append({'list': name, 'index': index, 'kind': item.get('kind'),
                        'params': _plain(item.get('params') or {}), 'result': result, 'detail': detail})
    return out


def host_verdict(*, signals: list[dict], conditions: list[dict], recheck: bool) -> dict | None:
    """Spec §7.4 steps 1–2: {'outcome', 'reason'} decided by the host, or None (the model must look).

    Any met invalidation condition invalidates, recheck or not. Otherwise, with
    no fired signal (user_input included) and no met review condition, a batch
    review maintains; a recheck never takes that shortcut.
    """
    met = [c for c in conditions if c.get('list') == 'invalidation' and c.get('result') == 'met']
    if met:
        return {'outcome': 'invalidate',
                'reason': '失效条件成立：' + REASON_JOIN.join(condition_registry.describe(c) for c in met)}
    if recheck:
        return None
    if any(s.get('fired') for s in signals):
        return None
    review = [c for c in conditions if c.get('list') == 'review_when']
    if any(c.get('result') == 'met' for c in review):
        return None
    texts = REASON_JOIN.join(condition_registry.describe(c) for c in review) or '无'
    return {'outcome': 'maintain', 'reason': f'无新信号，复查条件未满足（{texts}）'}


# ---------------------------------------------------------------- user inputs

def effective_inputs(rows: list[dict], cutoff: datetime) -> list[dict]:
    """Inputs valid at cutoff (spec §6.3): created at or before cutoff, not superseded by another
    such row; retract rows are never evidence. Oldest first."""
    visible = [r for r in rows if r['created_at'] <= cutoff]
    superseded = {str(r['supersedes_id']) for r in visible if r.get('supersedes_id') is not None}
    live = [r for r in visible if str(r['id']) not in superseded and r.get('kind') != 'retract']
    return sorted(live, key=lambda r: (r['created_at'], r['id']))


def select_new_inputs(inputs_new: list[dict]) -> tuple[list[dict], int]:
    """(included oldest first, omitted count): the newest inputs while both caps hold
    (10 items, 8000 characters of body in total). user_input_ids are the included ids."""
    ordered = sorted(inputs_new, key=lambda r: (r['created_at'], r['id']))
    included: list[dict] = []
    chars = 0
    for row in reversed(ordered):
        size = len(row.get('body') or '')
        if len(included) >= NEW_INPUTS_MAX or chars + size > NEW_INPUTS_CHARS:
            break
        included.append(row)
        chars += size
    included.reverse()
    return included, len(ordered) - len(included)


# ---------------------------------------------------------------- host diff (agent-3)

def host_diff(previous: dict, new: dict) -> list[dict]:
    """`[{field, before, after, host_only}]` of an agent-3 revision (spec D7): the model's fields in
    `CHANGE_FIELDS` order (`host_only: False`), then the host fields `valid_until` / `entry_cap` when they moved
    (`host_only: True`; they are never in the model's `changes`). `previous` / `new` are the check_plan outputs
    with the two host values added by the caller (`valid_until`, `entry_cap`; dates or ISO strings)."""
    items = [{**item, 'host_only': False} for item in plan_check.diff(previous, new, schema=AGENT_V3)]
    for field in plan_check.HOST_FIELDS:
        before, after = _iso(previous.get(field)), _iso(new.get(field))
        if before != after:
            items.append({'field': field, 'before': before, 'after': after, 'host_only': True})
    return items


# ---------------------------------------------------------------- holding status

INSTRUCTIONS = ('hold', 'sell_half', 'exit', 'no_plan')
# Verbatim (spec §7.3); the frontend keeps a copy and a test guards it.
INSTRUCTION_TEXT = {
    'hold': '继续持有',
    'sell_half': '到 T1：卖出一半，余下按移动止损',
    'exit': '次一交易日开盘卖出',
    'no_plan': '无计划持仓（回撤规则看护）',
}
EXIT_REASONS = ('stop_touched', 'time_stop', 'invalidation', 'agent_invalidate')
# Why an exit instruction was given, for the 「退出指令自 <日期> 起（<原因>）」 line.
EXIT_REASON_TEXT = {'stop_touched': '止损触及', 'time_stop': '时间止损', 'invalidation': '失效条件成立',
                    'agent_invalidate': 'Agent 判作废'}
REASON_MAX_CHARS = 300
_PCT = Decimal('0.01')


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator * 100).quantize(_PCT, rounding=ROUND_HALF_UP)


def _basis_factor(day: date, splits: list[tuple[date, Decimal]]) -> Decimal:
    return exits._basis_factor(day, sorted(splits))


def _number(value: Decimal) -> str:
    return f'{value:.2f}'


def maintain_reason(kind: str, **fields: Any) -> str:
    """The reason line of a holding status (spec §7.4), at most 300 characters.

    - `agent`: the Agent's own review reason, passed as `reason`.
    - `host_quiet`: 「无新信号，复查条件未满足（<条件>）；收盘 <价> 在止损上方 <x>%，未到 T1，持有 n/m 天」
      (fields `conditions` (texts), `close`, `pct`, `held`, `max_hold`, optional `t1_reached`).
    - `incomplete`: 「今日复查未完成（<原因>），按第 n 版继续持有」 (`why`, `version_no`).
    - `no_chain`: 「无 Agent 链，按 <规则基线 / 自定计划> 的止损与最长持有看护」 (`plan_label`).
    - `exiting`: 「退出指令自 <日期> 起（<原因>），仍持有 n 股」 (`since`, `why`, `quantity`).
    `note` appends 「；<note>」 (e.g. 「无法计算 1R」).
    """
    if kind == 'agent':
        text = str(fields['reason'])
    elif kind == 'host_quiet':
        conditions_text = REASON_JOIN.join(fields.get('conditions') or []) or '无'
        position = f"收盘 {_number(Decimal(str(fields['close'])))} 在止损上方 {_number(Decimal(str(fields['pct'])))}%"
        t1 = '已触及 T1' if fields.get('t1_reached') else '未到 T1'
        text = (f'无新信号，复查条件未满足（{conditions_text}）；{position}，{t1}，'
                f"持有 {fields['held']}/{fields['max_hold']} 天")
    elif kind == 'incomplete':
        text = f"今日复查未完成（{fields['why']}），按第 {fields['version_no']} 版继续持有"
    elif kind == 'no_chain':
        text = f"无 Agent 链，按 {fields['plan_label']} 的止损与最长持有看护"
    elif kind == 'exiting':
        quantity = format(Decimal(str(fields['quantity'])).normalize(), 'f')
        text = f"退出指令自 {_iso(fields['since'])} 起（{fields['why']}），仍持有 {quantity} 股"
    else:
        raise ValueError(f'unknown reason kind: {kind!r}')
    if fields.get('note'):
        text += f"；{fields['note']}"
    return text[:REASON_MAX_CHARS]


def holding_facts(facts: dict, *, session: date) -> dict:
    """The numbers of one position's status at S (spec §7.2 step 1, §7.3), no row, no instruction.

    facts: `quantity`, `avg_cost` (the ticker's open lots, positions.build), `first_buy_session` (the New York
    day of the earliest open buy), `bars` (the ticker's daily bars, oldest first, split-adjusted; the caller
    cuts them at the cutoff), `splits`, `plan` (the effective plan row: the chain's latest version, or the
    linked plan of a position with no chain; None = no plan), `initial_plan` (the plan row of the first linked
    buy, whose stop sets 1R, D20; None = unknown).

    Returns `last_close`, `stop`, `stop_mode` ('initial' / 'trailing', `exits.active_stop`), `target1`,
    `t1_reached` (including today), `t1_today`, `reached_1r` (None = cannot tell), `to_stop_pct`, `to_t1_pct`,
    `r_now`, `held_sessions`, `max_hold_sessions`, `remaining_sessions`, `time_stop`
    ('met' / 'not_met' / 'unknown', the `held_sessions_reached` judgement), `stop_touched` (None without S's
    bar), `exit_trigger` ('stop_touched' / 'time_stop' / None: the host steps 2–3 of §7.2) and `detail`.
    """
    first = facts['first_buy_session']
    quantity, avg_cost = Decimal(str(facts['quantity'])), Decimal(str(facts['avg_cost']))
    splits = list(facts.get('splits') or [])
    plan, initial = facts.get('plan'), facts.get('initial_plan')
    bars = sorted((b for b in facts.get('bars') or [] if first <= b['price_date'] <= session),
                  key=lambda b: b['price_date'])
    today = next((b for b in bars if b['price_date'] == session), None)
    detail: dict[str, Any] = {}
    days = trading_days_between(first, session)
    held = max(len(days), 1)
    have = {b['price_date'] for b in bars}
    bars_missing = today is None
    if bars_missing:
        detail['bars_missing'] = True
    last_close = Decimal(str(today['close'])) if today is not None else None

    stop = target1 = stop_mode = None
    t1_before = t1_today = False
    if plan is not None:
        window = bars if today is not None else [*bars, {'price_date': session, 'high': 0, 'low': 0, 'close': 0}]
        active = exits.active_stop(plan, window, avg_cost=avg_cost, splits=splits)
        stop, target1, stop_mode, t1_before = active['stop'], active['target1'], active['mode'], active['t1_reached']
        t1_today = (not t1_before) and today is not None and Decimal(str(today['high'])) >= target1
    t1_reached = None if plan is None else (t1_before or t1_today)

    reached_1r: bool | None = None
    risk: Decimal | None = None
    if initial is not None:
        risk = avg_cost - Decimal(str(initial['stop'])) / _basis_factor(initial['signal_date'], splits)
        if risk <= 0:
            risk = None
    if risk is not None:
        if any(Decimal(str(b['close'])) >= avg_cost + risk for b in bars):
            reached_1r = True
        elif all(day in have for day in days):
            reached_1r = False                      # a gap in the bars could hide the close that reached it
    r_now = ((last_close - avg_cost) / risk).quantize(_PCT, rounding=ROUND_HALF_UP) \
        if last_close is not None and risk is not None else None

    max_hold = None
    if plan is not None:
        max_hold = plan.get('max_hold_sessions')
        if max_hold is None:
            max_hold, detail['max_hold_default'] = plans.MAX_HOLD_DEFAULT, True
    time_stop = 'not_met'
    if max_hold is not None:
        position = {'held_sessions': held, 'reached_1r': reached_1r, 't1_reached': bool(t1_reached), 'mode': 'holding'}
        time_stop, _ = condition_registry.evaluate(
            condition_registry.max_hold_condition(max_hold),
            condition_registry.Facts(session=session, mode='holding', bars=[], bars_since_version=[], entry_low=None,
                                     entry_high=None, regime_label=None, sector_state=None, earnings_date=None,
                                     position=position))
    remaining = max(max_hold - held, 0) if max_hold is not None and reached_1r is False and not t1_reached else None

    stop_touched = None if (plan is None or today is None) else Decimal(str(today['low'])) <= stop
    exit_trigger = 'stop_touched' if stop_touched else ('time_stop' if time_stop == 'met' else None)
    return {
        'quantity': quantity, 'avg_cost': avg_cost, 'last_close': last_close, 'stop': stop, 'stop_mode': stop_mode,
        'target1': target1, 't1_reached': t1_reached, 't1_today': t1_today, 'reached_1r': reached_1r,
        'to_stop_pct': _pct(last_close - stop, last_close) if last_close and stop is not None else None,
        'to_t1_pct': _pct(target1 - last_close, last_close) if last_close and target1 is not None and not t1_reached
        else None,
        'r_now': r_now, 'held_sessions': held, 'max_hold_sessions': max_hold, 'remaining_sessions': remaining,
        'time_stop': time_stop, 'stop_touched': stop_touched, 'exit_trigger': exit_trigger, 'detail': detail,
    }


def holding_status(facts: dict, *, session: date) -> dict:
    """One `holding_status` row's values for a position at S (spec §7.3, D19, D20), pure.

    On top of `holding_facts`' inputs: `ticker`, `chain_signal_date`, `chain_state` ('holding' / 'exiting' /
    None), `plan_id`, `review_id`, `today_outcome` (today's review conclusion: maintain / revise / invalidate /
    skipped / error, None = no review), `exit_reason` (one of EXIT_REASONS, why today's invalidate or the
    standing exit instruction was given), `exit_since` (the session the exit instruction began), `sold_after_t1`,
    `previous` (the previous status row; an exit instruction survives a day without bars), `reason` (the
    stored reason when the caller composed it, else a §7.4 template), `plan_label`, `review_note`.

    `instruction`: `exit` when the chain is exiting or today's conclusion is invalidate; `sell_half` when T1
    first traded today and nothing was sold after it; `hold` for the other planned positions; `no_plan` for a
    position with no plan. `exit_reason` is set exactly when the instruction is `exit`.
    """
    values = holding_facts(facts, session=session)
    plan = facts.get('plan')
    chain_state_name = facts.get('chain_state')
    previous = facts.get('previous') or {}
    exit_reason = None
    if chain_state_name == 'exiting' or facts.get('today_outcome') == 'invalidate':
        instruction = 'exit'
        exit_reason = facts.get('exit_reason') or ('invalidation' if chain_state_name == 'exiting'
                                                     else 'agent_invalidate')
    elif values['detail'].get('bars_missing') and previous.get('instruction') == 'exit':
        instruction, exit_reason = 'exit', previous.get('exit_reason') or 'invalidation'
    elif plan is None:
        instruction = 'no_plan'
    elif values['t1_today'] and not facts.get('sold_after_t1'):
        instruction = 'sell_half'
    else:
        instruction = 'hold'
    if exit_reason is not None and exit_reason not in EXIT_REASONS:
        raise ValueError(f'unknown exit reason: {exit_reason!r}')

    reason = facts.get('reason')
    if not reason:
        if instruction == 'exit':
            reason = maintain_reason('exiting', since=facts.get('exit_since') or session,
                                     why=EXIT_REASON_TEXT[exit_reason], quantity=values['quantity'])
        elif plan is None:
            reason = INSTRUCTION_TEXT['no_plan']
        elif chain_state_name is None:
            reason = maintain_reason('no_chain', plan_label=facts.get('plan_label') or '规则基线')
        elif facts.get('today_outcome') in (None, 'skipped', 'error'):
            reason = maintain_reason('incomplete', why=facts.get('review_note') or '复查未执行',
                                     version_no=facts.get('version_no', '—'))
        else:
            reason = maintain_reason('host_quiet', conditions=facts.get('review_conditions') or [],
                                     close=values['last_close'] if values['last_close'] is not None else Decimal(0),
                                     pct=values['to_stop_pct'] if values['to_stop_pct'] is not None else Decimal(0),
                                     held=values['held_sessions'], max_hold=values['max_hold_sessions'] or '—',
                                     t1_reached=values['t1_reached'])
    if instruction == 'hold' and plan is not None and values['reached_1r'] is None and '无法计算 1R' not in reason:
        reason = maintain_reason('agent', reason=reason, note='无法计算 1R')
    missing_note = f'缺 {session.isoformat()} 的日线'
    if values['detail'].get('bars_missing') and missing_note not in reason:
        reason = maintain_reason('agent', reason=reason, note=missing_note)

    detail = dict(values['detail'])
    if facts.get('signals'):
        detail['signals'] = facts['signals']
    return {
        'ticker': facts.get('ticker'), 'session': session, 'chain_signal_date': facts.get('chain_signal_date'),
        'plan_id': facts.get('plan_id') if plan is not None else None, 'review_id': facts.get('review_id'),
        'quantity': values['quantity'], 'avg_cost': values['avg_cost'], 'last_close': values['last_close'],
        'stop': values['stop'], 'stop_mode': values['stop_mode'], 'target1': values['target1'],
        't1_reached': values['t1_reached'], 'reached_1r': values['reached_1r'],
        'to_stop_pct': values['to_stop_pct'], 'to_t1_pct': values['to_t1_pct'], 'r_now': values['r_now'],
        'held_sessions': values['held_sessions'], 'max_hold_sessions': values['max_hold_sessions'],
        'remaining_sessions': values['remaining_sessions'], 'instruction': instruction,
        'exit_reason': exit_reason, 'reason': reason[:REASON_MAX_CHARS], 'detail': detail,
        'timeline_version': TIMELINE_V2,
    }


# ---------------------------------------------------------------- previous chain evidence block
