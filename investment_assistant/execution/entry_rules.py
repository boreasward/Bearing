"""Entry types and the three open scenarios `entry-1` (v2.9 spec §5.2, §5.3). Pure functions.

Three entry types (D10) and, for every valid day, three situations by the day's open O against the entry
zone [L, U] with stop S: opens inside the zone, gaps above it, gaps below it. `scenarios` words them for the
page and the push, `simulate_entry` replays them for the shadow (`plan-shadow-2`); both read the one table
below, so the text and the replay cannot disagree (Review Focus 4). `void_when` lists when the plan stops
being executable.

Plan dicts carry `entry_low`, `entry_high`, `stop`, `entry_type`, `valid_from`, `valid_until` and, for the
share count, `sizing['shares']`; bars carry `price_date`, `open`, `high`, `low`. Levels must already be in the
bars' split basis (the shadow rescales them first).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from investment_assistant.execution import conditions as condition_registry

ENTRY_RULES_VERSION = 'entry-1'

ENTRY_TYPES = ('zone_limit', 'breakout_stop', 'next_open')
ENTRY_TYPE_TEXT = {'zone_limit': '区间限价', 'breakout_stop': '突破买入', 'next_open': '次日开盘'}
DEFAULT_ENTRY_TYPE = 'zone_limit'
OPEN_CASES = ('in_zone', 'gap_up', 'gap_down')
OPEN_CASE_TEXT = {'in_zone': '区间内', 'gap_up': '高开', 'gap_down': '低开'}

# Scenario wording, verbatim (spec §5.2). {L} {U} {S} {N} are the plan's entry_low, entry_high, stop and shares.
TEXT = {
    'in_zone': '开在 {L}–{U}：开盘买入 {N} 股',
    'gap_up': {
        'zone_limit': '高开过 {U}：不追，挂 {U} 限价等回落',
        'breakout_stop': '高开过 {U}：不追，挂 {U} 限价等回落',
        'next_open': '高开过 {U}：不买，本计划作废',
    },
    'gap_down': {
        'zone_limit': '低开：≤ {S} 作废；否则回升到 {L} 时买入',
        'breakout_stop': '低开：≤ {S} 作废；否则升破 {L} 时买入',
        'next_open': '低开过 {L}：不买，本计划作废',
    },
}
# What the case means for the order. `void` = the plan is dead for good; gap_down of the two limit types is
# conditional (O <= stop voids it, otherwise the stop-buy at L waits for the rebound).
ACTIONS = {
    'zone_limit': {'in_zone': 'buy_open', 'gap_up': 'limit_upper', 'gap_down': 'stop_buy_lower'},
    'breakout_stop': {'in_zone': 'buy_open', 'gap_up': 'limit_upper', 'gap_down': 'stop_buy_lower'},
    'next_open': {'in_zone': 'buy_open', 'gap_up': 'void', 'gap_down': 'void'},
}

VOID_REASONS = ('gap_below_stop', 'gap_above_zone', 'gap_below_zone')

# void_when texts (spec §5.3); the pinned examples are the validity, gap (limit types) and max_hold lines.
VOID_TEXT = {
    'validity': '有效期至 {E} 收盘仍未成交',
    'gap': {'zone_limit': '开盘 ≤ {S}', 'breakout_stop': '开盘 ≤ {S}',
            'next_open': '首个有效日开盘不在 {L}–{U} 内'},
    'max_hold': '持有 {N} 个交易日仍未到 1R → 次日开盘卖出',
    'stop': '收盘前触及止损 {S}（T1 之后为移动止损）',
}
HOLDING_PREFIX = '持仓后：'


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Any) -> str:
    return f'{_dec(value):.2f}'


def _shares(plan: dict) -> str:
    sizing = plan.get('sizing')
    shares = sizing.get('shares') if isinstance(sizing, dict) else plan.get('shares')
    return '—' if shares is None else str(int(shares))


def entry_type_of(plan: dict) -> str:
    """The plan's entry type; a plan without execution fields reads as `zone_limit` (spec §5.5)."""
    value = plan.get('entry_type')
    return value if value in ENTRY_TYPES else DEFAULT_ENTRY_TYPE


def _fill(template: str, plan: dict) -> str:
    return template.format(L=_money(plan['entry_low']), U=_money(plan['entry_high']), S=_money(plan['stop']),
                           N=_shares(plan))


def case_of(plan: dict, open_price: Any) -> str:
    """Which of the three situations an open price is: `in_zone`, `gap_up` (O > U) or `gap_down` (O < L)."""
    price = _dec(open_price)
    if price > _dec(plan['entry_high']):
        return 'gap_up'
    if price < _dec(plan['entry_low']):
        return 'gap_down'
    return 'in_zone'


def scenarios(plan: dict) -> list[dict]:
    """`[{case, action, text}]` for the three open cases, in `OPEN_CASES` order (spec §5.2)."""
    kind = entry_type_of(plan)
    out = [{'case': 'in_zone', 'action': ACTIONS[kind]['in_zone'], 'text': _fill(TEXT['in_zone'], plan)}]
    for case in ('gap_up', 'gap_down'):
        out.append({'case': case, 'action': ACTIONS[kind][case], 'text': _fill(TEXT[case][kind], plan)})
    return out


def void_when(plan: dict, *, conditions: list[dict], mode: str, entry_cap: date | None) -> list[dict]:
    """When this plan stops being executable, in order (spec §5.3): `[{source, text}]`.

    `validity` (the last valid day, or the entry-phase cap when it is earlier), `gap` (the open that kills the
    plan), `invalidation` (each of the latest version's conditions, with `last_result` when the caller attached
    one), `max_hold`, `stop`. A rule baseline passes `conditions=[]`, so it only has the other four. The
    `held_sessions_reached` condition is the host-injected `max_hold`, so it is never listed twice. In `holding`
    mode the entry-only lines (`validity`, `gap`) drop out and the two holding lines lose the 「持仓后：」 lead.
    """
    if mode not in ('entry', 'holding'):
        raise ValueError(f'unknown chain mode: {mode!r}')
    kind = entry_type_of(plan)
    out: list[dict] = []
    if mode == 'entry':
        valid_until = plan['valid_until']
        end = min(valid_until, entry_cap) if entry_cap is not None else valid_until
        out.append({'source': 'validity', 'text': VOID_TEXT['validity'].format(E=end.strftime('%m-%d'))})
        out.append({'source': 'gap', 'text': _fill(VOID_TEXT['gap'][kind], plan)})
    for item in conditions or []:
        if item.get('kind') == 'held_sessions_reached':
            continue
        out.append({'source': 'invalidation', 'text': condition_registry.describe(item),
                    'last_result': item.get('last_result')})
    lead = HOLDING_PREFIX if mode == 'entry' else ''
    max_hold = plan.get('max_hold_sessions')
    if max_hold is not None:
        out.append({'source': 'max_hold', 'text': lead + VOID_TEXT['max_hold'].format(N=int(max_hold))})
    out.append({'source': 'stop', 'text': lead + _fill(VOID_TEXT['stop'], plan)})
    return out


# ---------------------------------------------------------------- replay

def _result(status: str, **extra: Any) -> dict:
    return {'status': status, 'fill_date': None, 'fill_price': None, 'void_reason': None, 'void_date': None,
            'case': None, **extra}


def simulate_entry(plan: dict, bars: list[dict]) -> dict:
    """Replay the entry on daily bars (oldest first): `{status, fill_date, fill_price, void_reason, void_date,
    case}`, `status` one of `filled`, `voided`, `expired`, `pending`.

    Per valid day by the open O (spec §5.2): inside the zone fills at O; above it, the limit stays at U and
    only a pullback to U fills (at U); below it, O <= S voids the plan (`gap_below_stop`), otherwise only a
    rebound to L fills (at L). `next_open` looks at the first valid day only: inside the zone it fills at O,
    any other open voids (`gap_above_zone`, `gap_below_zone`, `gap_below_stop` when O <= S). A plan with no
    fill and no void is `expired` once the last valid day has a bar, else `pending`.

    Known limitation: a `next_open` plan whose first valid day has no bar (a data gap) is neither filled nor
    voided: it stays `pending` and turns `expired` after the last valid day, never `voided`.
    """
    low, high, stop = _dec(plan['entry_low']), _dec(plan['entry_high']), _dec(plan['stop'])
    kind = entry_type_of(plan)
    window = [b for b in bars if plan['valid_from'] <= b['price_date'] <= plan['valid_until']]
    window.sort(key=lambda b: b['price_date'])
    latest_day = max((b['price_date'] for b in bars), default=None)

    candidates = window
    if kind == 'next_open':
        candidates = window[:1] if window and window[0]['price_date'] == plan['valid_from'] else []

    for bar in candidates:
        op, hi, lo = _dec(bar['open']), _dec(bar['high']), _dec(bar['low'])
        case = case_of({'entry_low': low, 'entry_high': high}, op)
        day = bar['price_date']
        if kind == 'next_open':
            if case == 'in_zone':
                return _result('filled', fill_date=day, fill_price=op, case=case)
            reason = 'gap_below_stop' if op <= stop else ('gap_above_zone' if case == 'gap_up' else 'gap_below_zone')
            return _result('voided', void_reason=reason, void_date=day, case=case)
        if case == 'in_zone':
            return _result('filled', fill_date=day, fill_price=op, case=case)
        if case == 'gap_up':
            if lo <= high:                              # pulled back to the upper limit
                return _result('filled', fill_date=day, fill_price=high, case=case)
            continue
        if op <= stop:
            return _result('voided', void_reason='gap_below_stop', void_date=day, case=case)
        if hi >= low:                                   # rebounded to the lower edge
            return _result('filled', fill_date=day, fill_price=low, case=case)

    expired = latest_day is not None and latest_day >= plan['valid_until']
    return _result('expired' if expired else 'pending')
