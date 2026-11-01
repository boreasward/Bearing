"""Invalidation and review conditions `condition-2` (v2.5 spec §4.2; v2.9 spec §7.1). Pure functions.

`condition-2` is `condition-1` plus `held_sessions_reached` (the time stop). The eleven `condition-1` kinds
judge exactly as before.

An agent-2 plan declares invalidation conditions (1–5) and review conditions
(0–3) picked from this registry; the host validates them when the plan is
checked and evaluates them on every daily review. The caller assembles Facts
for the judgment window (invalidation: the session after the version's
as_of_session through S; review: after the last review through S), so this
module never reads the database. Missing data is always 'unknown', never 'met'.

Normalized params: price -> Decimal quantized to cents, date -> date,
sessions / window -> int, value -> float, label / form -> str. evaluate and
describe also accept the JSON form read back from storage (price as a string
or number, date as an ISO string).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from investment_assistant.data.calendar import add_trading_days, trading_days_between
from investment_assistant.execution.citations import Rejected

CONDITIONS_V1 = 'condition-1'
CONDITIONS_VERSION = 'condition-2'

PRICE_BAND = Decimal('0.5')          # price params within ±50 % of the last close at generation
DATE_HORIZON_DAYS = 60               # date params at most 60 calendar days after as_of_session
SESSIONS_RANGE = (1, 10)
HELD_SESSIONS_RANGE = (3, 30)         # held_sessions_reached.sessions (the plan's max hold lies in 5–20)
REGIME_LABELS = ('risk_on', 'neutral', 'risk_off')
FILING_FORMS = ('8-K', '10-Q', '10-K')
RS_WINDOWS = (20, 60)
_CENT = Decimal('0.01')
_REASON = 'invalid_condition'


@dataclass(frozen=True)
class ConditionDef:
    kind: str
    params: tuple[str, ...]
    text: str
    invalidation: bool
    review: bool


CONDITIONS_ALL: dict[str, ConditionDef] = {d.kind: d for d in (
    ConditionDef('close_below', ('price',), '收盘跌破 {price}', True, True),
    ConditionDef('close_above', ('price',), '收盘突破 {price}', True, True),
    ConditionDef('no_entry_by', ('date',), '{date} 收盘前未进入入场区', True, False),
    ConditionDef('no_entry_before_earnings', (), '财报前未进入入场区', True, False),
    ConditionDef('earnings_within', ('sessions',), '距财报不足 {sessions} 个交易日', True, True),
    ConditionDef('regime_is', ('label',), '区制变为 {label}', True, True),
    ConditionDef('sector_weak', (), '所属行业转弱', True, True),
    ConditionDef('rs_below', ('window', 'value'), '相对 SPY {window} 日强弱低于 {value}', True, True),
    ConditionDef('new_filing', ('form',), '出现新的 {form}', True, True),
    ConditionDef('date_reached', ('date',), '到 {date} 再看', False, True),
    ConditionDef('next_session', (), '下一交易日', False, True),
    ConditionDef('held_sessions_reached', ('sessions',), '持有 {sessions} 个交易日仍未到 1R', True, True),
)}
# The condition-1 view (eleven kinds): what the live agent-2 prompt table (`graph._conditions_table`) renders and
# agent-2 callers see. condition-2's twelfth kind lives only in CONDITIONS_ALL, which validate / evaluate /
# describe and the agent-3 prompt use; Task 3 builds the agent-3 table from CONDITIONS_ALL.
CONDITIONS: dict[str, ConditionDef] = {k: d for k, d in CONDITIONS_ALL.items() if k != 'held_sessions_reached'}


@dataclass
class Facts:
    """What the host knows at review session S, already cut to the judgment window.

    Missing-data contract (spec §4.2: missing data -> 'unknown', never 'met'):
    - filings: None means the SEC filing list is unavailable -> new_filing is
      'unknown'; an empty list means "no filings in the window" -> 'not_met'.
      The caller must pass a list (possibly empty) whenever the list was read.
    - as_of_session: the version's generation session. no_entry_by and
      no_entry_before_earnings can only be 'met' when bars_since_version holds
      a bar for every trading day from the session after as_of_session through
      the required end; without as_of_session, or with any gap, they are
      'unknown' (a bar that entered the zone still makes them 'not_met').
    """
    session: date
    mode: str                                   # 'entry' | 'holding'
    bars: list[dict]                            # judgment window, old -> new, Decimal prices
    bars_since_version: list[dict]              # every bar after the version was generated
    entry_low: Decimal | None
    entry_high: Decimal | None
    regime_label: str | None
    sector_state: str | None
    earnings_date: date | None
    filings: list[dict] | None = None                           # None = filing list unavailable
    rs: dict[int, float | None] = field(default_factory=dict)  # tools.indicators' rs20 / rs60
    as_of_session: date | None = None                          # the version's generation session
    # held_sessions_reached (v2.9 spec §7.1): the open position as the host computed it for S, or None when it
    # has none / the caller did not build it. Keys: held_sessions (int), reached_1r (bool | None = unknown),
    # t1_reached (bool, optional: after T1 the time stop no longer applies, R9), mode (optional override).
    position: dict | None = None


# ---------------------------------------------------------------- params

def _price(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number.quantize(_CENT, rounding='ROUND_HALF_UP')


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value if type(value) is date else None      # a datetime is not a session date
    if isinstance(value, str):
        try:
            return date.fromisoformat(value) if len(value) == 10 else None
        except ValueError:
            return None
    return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _normalize(name: str, raw: Any, *, last_close: Decimal, as_of_session: date, kind: str | None = None) -> Any:
    """One validated param in normalized form; None when it is not valid."""
    if name == 'price':
        price = _price(raw)
        if price is None or not last_close * (1 - PRICE_BAND) <= price <= last_close * (1 + PRICE_BAND):
            return None
        return price
    if name == 'date':
        day = _date(raw)
        if day is None or not as_of_session < day <= as_of_session + timedelta(days=DATE_HORIZON_DAYS):
            return None
        return day
    if name == 'sessions':
        n = _int(raw)
        low, high = HELD_SESSIONS_RANGE if kind == 'held_sessions_reached' else SESSIONS_RANGE
        return n if n is not None and low <= n <= high else None
    if name == 'window':
        n = _int(raw)
        return n if n in RS_WINDOWS else None
    if name == 'value':
        number = _float(raw)
        return number if number is not None and -1.0 <= number <= 1.0 else None
    if name == 'label':
        return raw if isinstance(raw, str) and raw in REGIME_LABELS else None
    if name == 'form':
        return raw if isinstance(raw, str) and raw in FILING_FORMS else None
    return None


def validate(item: dict, *, use: Literal['invalidation', 'review'], last_close: Decimal,
             as_of_session: date) -> dict:
    """Normalized {kind, params, text, refs?}; any problem raises Rejected('invalid_condition').

    text is the host rendering of the normalized item (describe), not the model's
    own wording; refs are kept untouched when present (their existence is the
    plan check's job).
    """
    if use not in ('invalidation', 'review'):
        raise ValueError(f'unknown condition use: {use!r}')
    if not isinstance(item, dict):
        raise Rejected(_REASON)
    definition = CONDITIONS_ALL.get(item.get('kind')) if isinstance(item.get('kind'), str) else None
    if definition is None or not getattr(definition, use):
        raise Rejected(_REASON)
    raw = item.get('params', {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) != set(definition.params):
        raise Rejected(_REASON)
    params: dict[str, Any] = {}
    for name in definition.params:
        value = _normalize(name, raw[name], last_close=last_close, as_of_session=as_of_session,
                           kind=definition.kind)
        if value is None:
            raise Rejected(_REASON)
        params[name] = value
    out: dict[str, Any] = {'kind': definition.kind, 'params': params}
    out['text'] = describe(out)
    if 'refs' in item:
        out['refs'] = item['refs']
    return out


# ---------------------------------------------------------------- rendering

def _show(name: str, value: Any) -> str:
    if name == 'price':
        price = _price(value)
        return f'{price:.2f}' if price is not None else str(value)
    if name == 'date':
        day = _date(value)
        return day.isoformat() if day is not None else str(value)
    if name == 'value':
        number = _float(value)
        return f'{number:g}' if number is not None else str(value)
    return str(value)


def describe(item: dict) -> str:
    """The registry template filled with the item's params (page text, host reasons)."""
    definition = CONDITIONS_ALL.get(item.get('kind'))
    if definition is None:
        return str(item.get('kind'))
    params = item.get('params') or {}
    return definition.text.format(**{name: _show(name, params.get(name)) for name in definition.params})


# ---------------------------------------------------------------- evaluation

def _intersects(bar: dict, low: Decimal, high: Decimal) -> bool:
    return bar['low'] <= high and bar['high'] >= low


def _first_entry(bars: list[dict], low: Decimal, high: Decimal, *, through: date | None = None) -> dict | None:
    for bar in bars:
        if through is not None and bar['price_date'] > through:
            break
        if _intersects(bar, low, high):
            return bar
    return None


def _missing_sessions(facts: Facts, end: date) -> list[date] | None:
    """Trading days in (as_of_session, end] with no bar; None when as_of_session is unknown."""
    if facts.as_of_session is None:
        return None
    have = {bar['price_date'] for bar in facts.bars_since_version}
    return [d for d in trading_days_between(facts.as_of_session + timedelta(days=1), end) if d not in have]


def _coverage_gap(facts: Facts, end: date) -> str | None:
    """None when bars cover every trading day after the version through end; else the reason."""
    missing = _missing_sessions(facts, end)
    if missing is None:
        return '缺少版本生成交易日'
    if missing:
        return f'缺少 {len(missing)} 个交易日的日线（首个 {missing[0].isoformat()}）'
    return None


def _zone(facts: Facts) -> str:
    return f'{facts.entry_low:.2f}–{facts.entry_high:.2f}'


def _eval_close(kind: str, params: dict, facts: Facts) -> tuple[str, str]:
    price = _price(params.get('price'))
    if price is None:
        return 'unknown', '参数无效'
    if not facts.bars:
        return 'unknown', '判定窗口内没有日线'
    below = kind == 'close_below'
    for bar in facts.bars:
        close = bar['close']
        if (close < price) if below else (close > price):
            sign = '<' if below else '>'
            return 'met', f'{bar["price_date"].isoformat()} 收盘 {close:.2f} {sign} {price:.2f}'
    extreme = min(b['close'] for b in facts.bars) if below else max(b['close'] for b in facts.bars)
    label = '最低' if below else '最高'
    return 'not_met', f'窗口内 {len(facts.bars)} 根日线{label}收盘 {extreme:.2f}'


def _eval_no_entry_by(params: dict, facts: Facts) -> tuple[str, str]:
    deadline = _date(params.get('date'))
    if deadline is None:
        return 'unknown', '参数无效'
    if facts.mode == 'holding':
        return 'not_met', '持仓期不适用'
    if facts.session is None:
        return 'unknown', '缺少复查交易日'
    if facts.session < deadline:
        return 'not_met', f'未到 {deadline.isoformat()}'
    if facts.entry_low is None or facts.entry_high is None:
        return 'unknown', '缺少入场区间'
    hit = _first_entry(facts.bars_since_version, facts.entry_low, facts.entry_high, through=deadline)
    if hit is not None:
        return 'not_met', f'{hit["price_date"].isoformat()} 进入入场区 {_zone(facts)}'
    if (gap := _coverage_gap(facts, min(deadline, facts.session))) is not None:
        return 'unknown', gap
    return 'met', f'截至 {deadline.isoformat()} 未进入入场区 {_zone(facts)}'


def _eval_no_entry_before_earnings(facts: Facts) -> tuple[str, str]:
    if facts.mode == 'holding':
        return 'not_met', '持仓期不适用'
    if facts.earnings_date is None:
        return 'unknown', '财报日未知'
    if facts.session is None:
        return 'unknown', '缺少复查交易日'
    next_session = add_trading_days(facts.session, 1)
    if next_session < facts.earnings_date:
        return 'not_met', f'财报日 {facts.earnings_date.isoformat()} 未临近'
    if facts.entry_low is None or facts.entry_high is None:
        return 'unknown', '缺少入场区间'
    hit = _first_entry(facts.bars_since_version, facts.entry_low, facts.entry_high)
    if hit is not None:
        return 'not_met', f'{hit["price_date"].isoformat()} 进入入场区 {_zone(facts)}'
    if (gap := _coverage_gap(facts, facts.session)) is not None:
        return 'unknown', gap
    return 'met', f'财报日 {facts.earnings_date.isoformat()} 前未进入入场区 {_zone(facts)}'


def _eval_earnings_within(params: dict, facts: Facts) -> tuple[str, str]:
    sessions = _int(params.get('sessions'))
    if sessions is None:
        return 'unknown', '参数无效'
    if facts.earnings_date is None:
        return 'unknown', '财报日未知'
    if facts.session is None:
        return 'unknown', '缺少复查交易日'
    horizon = add_trading_days(facts.session, sessions)
    earnings = facts.earnings_date.isoformat()
    if facts.session < facts.earnings_date <= horizon:
        return 'met', f'财报日 {earnings} 在 {sessions} 个交易日内'
    return 'not_met', f'财报日 {earnings} 不在 {sessions} 个交易日内'


def _eval_regime(params: dict, facts: Facts) -> tuple[str, str]:
    label = params.get('label')
    if facts.regime_label is None or facts.regime_label == 'insufficient':
        return 'unknown', '区制不可用' if facts.regime_label is None else '区制样本不足'
    state = 'met' if facts.regime_label == label else 'not_met'
    return state, f'当前区制 {facts.regime_label}'


def _eval_sector(facts: Facts) -> tuple[str, str]:
    if facts.sector_state is None:
        return 'unknown', '行业未映射或状态不可用'
    return ('met' if facts.sector_state == 'weak' else 'not_met'), f'行业状态 {facts.sector_state}'


def _eval_rs(params: dict, facts: Facts) -> tuple[str, str]:
    window = _int(params.get('window'))
    threshold = _float(params.get('value'))
    if window is None or threshold is None:
        return 'unknown', '参数无效'
    rs = (facts.rs or {}).get(window)
    if rs is None:
        return 'unknown', f'rs{window} 不可用'
    state = 'met' if rs < threshold else 'not_met'
    return state, f'rs{window} = {rs:.4f}'


def _eval_filing(params: dict, facts: Facts) -> tuple[str, str]:
    form = params.get('form')
    if facts.filings is None:
        return 'unknown', 'SEC 文件列表不可用'
    hits = [f for f in facts.filings if f.get('form') == form]
    if not hits:
        return 'not_met', f'窗口内没有 {form}'
    latest = hits[-1].get('filing_date')
    when = latest.isoformat() if isinstance(latest, date) else str(latest)
    return 'met', f'窗口内有 {len(hits)} 份 {form}（{when}）'


def _eval_date_reached(params: dict, facts: Facts) -> tuple[str, str]:
    day = _date(params.get('date'))
    if day is None:
        return 'unknown', '参数无效'
    if facts.session is None:
        return 'unknown', '缺少复查交易日'
    if facts.session >= day:
        return 'met', f'已到 {day.isoformat()}'
    return 'not_met', f'未到 {day.isoformat()}'


def _eval_held_sessions(params: dict, facts: Facts) -> tuple[str, str]:
    """Time stop (spec §7.1, R9): met when the position has been held `sessions` days and never closed at or
    above cost + 1R; unknown when 1R cannot be judged; not_met in the entry phase, after T1, or too early."""
    sessions = _int(params.get('sessions'))
    if sessions is None:
        return 'unknown', '参数无效'
    position = facts.position or {}
    mode = position.get('mode', facts.mode)
    if mode != 'holding':
        return 'not_met', '入场期不适用'
    held = position.get('held_sessions')
    if held is None:
        return 'unknown', '缺少持有天数'
    if position.get('t1_reached'):
        return 'not_met', '已触及 T1，时间止损不再适用'
    if held < sessions:
        return 'not_met', f'已持有 {held} 个交易日，不足 {sessions}'
    reached = position.get('reached_1r')
    if reached is None:
        return 'unknown', '无法计算 1R'
    if reached:
        return 'not_met', f'已持有 {held} 个交易日，但收盘到过 1R'
    return 'met', f'已持有 {held} 个交易日，收盘从未到 1R'


def max_hold_condition(sessions: int) -> dict:
    """The host-injected invalidation condition of a plan's max hold (spec D16, §7.1): the model cannot write it."""
    return {'source': 'max_hold', 'kind': 'held_sessions_reached', 'params': {'sessions': sessions}}


def evaluate(item: dict, facts: Facts) -> tuple[str, str]:
    """('met' | 'not_met' | 'unknown', detail) for one condition at facts.session."""
    kind = item.get('kind') if isinstance(item, dict) else None
    if kind not in CONDITIONS_ALL:
        return 'unknown', f'未登记的条件 {kind}'
    params = item.get('params') or {}
    if kind in ('close_below', 'close_above'):
        return _eval_close(kind, params, facts)
    if kind == 'no_entry_by':
        return _eval_no_entry_by(params, facts)
    if kind == 'no_entry_before_earnings':
        return _eval_no_entry_before_earnings(facts)
    if kind == 'earnings_within':
        return _eval_earnings_within(params, facts)
    if kind == 'regime_is':
        return _eval_regime(params, facts)
    if kind == 'sector_weak':
        return _eval_sector(facts)
    if kind == 'rs_below':
        return _eval_rs(params, facts)
    if kind == 'new_filing':
        return _eval_filing(params, facts)
    if kind == 'date_reached':
        return _eval_date_reached(params, facts)
    if kind == 'held_sessions_reached':
        return _eval_held_sessions(params, facts)
    return 'met', '每个交易日都复查'                    # next_session
