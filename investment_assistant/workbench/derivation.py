"""Plan derivation chain (v2.4): expand how every stored plan number follows from its frozen inputs.

Pure functions, no database, no network, no clock. Each step states its formula and the substituted
values, recomputes the result with ``Decimal`` from exactly those values, and compares it with the stored
value. This module is a second, independent implementation of the ``plan-1`` math: it never calls
``plans.py`` computation functions, and its constants are declared here (a test asserts they equal the
ones in ``plans.py``, so changing a formula without a new ``PLAN_VERSION`` fails the suite).

Own plans keep the user's four levels as inputs and agent plans check them against the agent run's output;
both size from the entry band midpoint. ``agent-2`` version plans (v2.5 §5.2) derive like ``agent-1`` except the
validity window, which follows ``inputs.as_of_session`` and the entry-phase cap ``inputs.chain_entry_cap``, and
they check the plan's ``agent_plan_versions`` row (missing → ``incomplete``). ``plan-2`` (v2.9 §5.4) derives like
``plan-1`` plus its two execution fields (``entry_type``, ``max_hold_sessions``) against the rule constants; ``agent-3``
derives like ``agent-2`` plus the two execution fields against the agent run's output, with the entry-phase cap
taken from the chain's ``chain_retriggers`` rows written by the version's cutoff (the later of the signal date + 10
sessions and every trigger's ``entry_cap``, D6). The trigger section
(``derive_trigger``) reviews each candidate's frozen values against its rule's condition clauses; a violated
clause is a mismatch.

Rounding: ``atr`` and ``ref_price`` to 6 places, the four price levels to cents (both half-up), the three
share caps floored. Comparison is exact ``Decimal`` equality. Numbers leave this module as strings,
dates as ISO text, reasons and warnings as sorted lists.
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dtime
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from operator import ge, gt, le, lt, ne
from typing import Any, Callable, NamedTuple
from zoneinfo import ZoneInfo

from investment_assistant.core.versions import DERIVATION_VERSION as DERIVATION_VERSION
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.core import tx as core_tx

REGISTERED_VERSIONS = ('plan-1', 'plan-2', 'agent-1', 'agent-2', 'agent-3')

ENTRY_ATR = Decimal('0.5')
STOP_ATR = Decimal('2')
TARGET_ATR = Decimal('4')
TRAIL_ATR_MULTIPLE = Decimal('3')
VALID_SESSIONS = 3
EARNINGS_WARN_SESSIONS = 10
ATR_PERIOD = 14
MIN_BARS = ATR_PERIOD + 1
CHAIN_ENTRY_SESSIONS = 10      # agent-2 entry-phase chain cap; a test keeps it equal to timeline's
RULE_ENTRY_TYPE = 'zone_limit'            # plan-2 execution fields (v2.9 §5.4); a test keeps them equal to plans'
RULE_MAX_HOLD_SESSIONS = 10
MAX_HOLD_RANGE = (3, 30)                  # trade_plans.max_hold_sessions CHECK
ENTRY_TYPES = ('zone_limit', 'breakout_stop', 'next_open')

EVIDENCE_TEXT = '规则 v1 与 `plan-1` 都未经验证（Phase 0 Gate HOLD）。推导一致只说明算术正确，不说明规则有效。'
TRIGGER_NOTE = '触发段只复核候选冻结的取值是否满足阈值，不从原始日线重算均线。'

_CENT = Decimal('0.01')
_SIX = Decimal('0.000001')
_NO_STORED = object()   # a step with nothing stored to compare against
_NONE_TEXT = '无'


@dataclass
class Rows:
    """The raw rows a plan's ``inputs`` point at, read back by id (Decimals for numbers)."""

    bars: list[dict] | None = None          # in ``inputs.bar_ids`` order
    earnings: dict | None = None
    snapshot: dict | None = None
    regime: dict | None = None
    agent_run: dict | None = None
    rule_plan: dict | None = None
    version: dict | None = None             # agent-2 / agent-3: the plan's agent_plan_versions row
    retriggers: list[dict] = field(default_factory=list)   # agent-3: the chain's chain_retriggers rows
    candidates: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # e.g. ``price_bars:<id>``


# ---------------------------------------------------------------- value helpers

def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _opt_dec(value: Any) -> Decimal | None:
    return None if value is None else _dec(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _fmt(value: Decimal) -> str:
    return format(value, 'f')


def _ser(value: Any) -> Any:
    """Result/stored value as emitted: strings for numbers and dates, sorted lists, None stays None."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return _fmt(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted({str(v) for v in value})
    return str(value)


def _sub(value: Any) -> str:
    """A substituted value as display text."""
    if value is None:
        return _NONE_TEXT
    if isinstance(value, (list, tuple, set, frozenset)):
        return ', '.join(sorted({str(v) for v in value})) or _NONE_TEXT
    return _ser(value)


def _norm(value: Any) -> Any:
    """Comparison form: reasons and warnings compare as sets, everything else exactly."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(v) for v in value)
    return value


def _jsonable(value: Any) -> Any:
    """Frozen rows for display: numbers become strings, dates ISO, ids text."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Decimal):
        return _fmt(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


def _quantize(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def _floor_div(numerator: Decimal, denominator: Decimal) -> int | None:
    """⌊numerator / denominator⌋; None when the divisor is not positive (cannot size)."""
    if denominator <= 0:
        return None
    return int((numerator / denominator).to_integral_value(rounding=ROUND_FLOOR))


def _step(key: str, label: str, formula: str, substitutions: dict[str, Any], result: Any,
          stored: Any = _NO_STORED, source: str = 'formula', refs: list[str] | None = None) -> dict[str, Any]:
    consistent: bool | None
    if stored is _NO_STORED:
        consistent, stored_out = None, None
    else:
        consistent, stored_out = _norm(result) == _norm(stored), _ser(stored)
    step = {'key': key, 'label': label, 'formula': formula,
            'substitutions': {k: _sub(v) for k, v in substitutions.items()},
            'result': _ser(result), 'stored': stored_out, 'source': source, 'consistent': consistent}
    if refs is not None:   # the agent's citations for a level
        step['refs'] = refs
    return step


# ---------------------------------------------------------------- inputs section

def _bar_view(bar: dict[str, Any]) -> dict[str, Any]:
    return {'id': _jsonable(bar.get('id')), 'price_date': _jsonable(bar.get('price_date')),
            'open': _jsonable(bar.get('open')), 'high': _jsonable(bar.get('high')),
            'low': _jsonable(bar.get('low')), 'close': _jsonable(bar.get('close')),
            'revision': _jsonable(bar.get('revision'))}


def _inputs_view(plan: dict[str, Any], rows: Rows) -> dict[str, Any]:
    inp = plan.get('inputs') or {}
    ctx = inp.get('context') or {}
    sizing = plan.get('sizing') or {}
    exposure = inp.get('exposure') or {}
    regime = ctx.get('regime')
    if rows.regime is not None:
        regime = {k: rows.regime.get(k) for k in ('id', 'label', 'calc_version') if k in rows.regime}
    ref = rows.bars[-1] if rows.bars else None
    return _jsonable({
        'bar_ids': list(inp.get('bar_ids') or []),
        'bars': [_bar_view(b) for b in rows.bars] if rows.bars is not None else None,
        'bars_note': '拆股调整、未分红调整',
        'ref_price': {'value': plan.get('ref_price'), 'source_bar_id': ref.get('id') if ref else None},
        'earnings': ({'id': rows.earnings.get('id'), 'status': rows.earnings.get('status'),
                      'earnings_date': rows.earnings.get('earnings_date'),
                      'fetched_at': rows.earnings.get('fetched_at')}
                     if rows.earnings is not None else None),
        'earnings_note': None if rows.earnings is not None else '无记录',
        'snapshot': ({'id': rows.snapshot.get('id'), 'as_of': rows.snapshot.get('as_of'),
                      'equity': rows.snapshot.get('equity')} if rows.snapshot is not None else None),
        'equity_source': sizing.get('equity_source', 'snapshot'),
        'snapshot_note': None if rows.snapshot is not None else '无快照（no_equity）',
        'exposure': {'ticker_value': exposure.get('ticker_value'), 'total_value': exposure.get('total_value'),
                     'note': '生成时冻结，不重算'},
        'sizing_params': {'risk_pct': sizing.get('risk_pct'), 'single_cap': sizing.get('single_cap'),
                          'total_cap': sizing.get('total_cap'), 'note': '取计划冻结值，不读当前配置'},
        'regime': regime,
        'sector': ctx.get('sector'),
        'next_earnings': ctx.get('next_earnings'),
        'pool_tier': ctx.get('pool_tier'),
        'source': ctx.get('source'),
        'screen': ctx.get('screen'),
        'agent_run': rows.agent_run,
    })


# ---------------------------------------------------------------- building blocks shared by every kind

Steps = list[dict[str, Any]]

LEVEL_LABELS = {'entry_low': '入场下沿', 'entry_high': '入场上沿', 'stop': '止损', 'target1': '目标一'}
_BREAKEVEN_NOTE = '移动止损退化为保本'


def _bars_complete(plan: dict[str, Any], rows: Rows, *, exact: bool) -> bool:
    """The bars read back are exactly the ones ``inputs.bar_ids`` name, in order.

    A rule plan always freezes 15 bars; own and agent plans freeze up to 15 (fewer when history is short).
    """
    ids = [str(i) for i in (plan.get('inputs') or {}).get('bar_ids') or []]
    if rows.bars is None or [str(b.get('id')) for b in rows.bars] != ids:
        return False
    return len(ids) == MIN_BARS if exact else 1 <= len(ids) <= MIN_BARS


def _frozen_missing(plan: dict[str, Any], rows: Rows) -> list[str]:
    """Rows or frozen values the sizing and earnings steps need but the plan's ``inputs`` could not supply."""
    inp = plan.get('inputs') or {}
    missing: list[str] = []
    if inp.get('snapshot_id') is not None and rows.snapshot is None:
        table = 'account_equity_days' if (plan.get('sizing') or {}).get('equity_source') == 'ledger' else 'account_snapshots'
        missing.append(f"{table}:{inp['snapshot_id']}")
    if inp.get('earnings_date_id') is not None and rows.earnings is None:
        missing.append(f"earnings_dates:{inp['earnings_date_id']}")
    if inp.get('snapshot_id') is not None and rows.snapshot is not None:
        exposure = inp.get('exposure') or {}
        missing += [f'exposure.{k}' for k in ('ticker_value', 'total_value') if exposure.get(k) is None]
        sizing = plan.get('sizing') or {}
        missing += [f'sizing.{k}' for k in ('risk_pct', 'single_cap', 'total_cap') if sizing.get(k) is None]
    return missing


def _snapshot_ok(missing: list[str]) -> bool:
    return not any(m.startswith(('account_snapshots:', 'account_equity_days:', 'exposure.', 'sizing.')) for m in missing)


def _earnings_ok(missing: list[str]) -> bool:
    return not any(m.startswith('earnings_dates:') for m in missing)


def _atr_steps(bars: list[dict[str, Any]], stored: Decimal | None) -> tuple[Steps, Decimal]:
    """TR1..TR14 from the last 15 bars, then their mean (``plan-1`` has exactly 14 true ranges)."""
    window = bars[-MIN_BARS:]
    steps: Steps = []
    trs: list[Decimal] = []
    for i in range(1, MIN_BARS):
        high, low, prev_close = _dec(window[i]['high']), _dec(window[i]['low']), _dec(window[i - 1]['close'])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        steps.append(_step(f'tr_{i}', f'真实波幅 TR{i}', 'max(H − L, |H − C前|, |L − C前|)',
                           {'H': high, 'L': low, 'C前': prev_close}, tr))
    atr = _quantize(sum(trs, Decimal(0)) / ATR_PERIOD, _SIX)
    steps.append(_step('atr', 'ATR14', '(tr_1 + … + tr_14) / 14，四舍五入到 6 位（15 根日线恰好 14 个 TR，'
                                       'Wilder 递推退化为算术平均）',
                       {f'tr_{i}': tr for i, tr in enumerate(trs, 1)}, atr, stored))
    return steps, atr


def _ref_price_step(plan: dict[str, Any], bars: list[dict[str, Any]]) -> tuple[dict[str, Any], Decimal]:
    ref_price = _quantize(_dec(bars[-1]['close']), _SIX)
    return (_step('ref_price', '参考价', 'close(最后一根)，四舍五入到 6 位', {'close': _dec(bars[-1]['close'])},
                  ref_price, _opt_dec(plan.get('ref_price'))), ref_price)


def _trail_step(plan: dict[str, Any]) -> dict[str, Any]:
    return _step('trail_atr_multiple', '移动止损倍数', '常量 3', {'TRAIL_ATR_MULTIPLE': TRAIL_ATR_MULTIPLE},
                 TRAIL_ATR_MULTIPLE, _opt_dec(plan.get('trail_atr_multiple')))


def _validity_steps(plan: dict[str, Any], signal: date) -> tuple[Steps, date, date]:
    valid_from = add_trading_days(signal, 1)
    valid_until = add_trading_days(signal, VALID_SESSIONS)
    return ([_step('valid_from', '有效期起', '信号日后第 1 个交易日', {'signal_date': signal}, valid_from,
                   _date(plan['valid_from'])),
             _step('valid_until', '有效期止', '信号日后第 3 个交易日',
                   {'signal_date': signal, 'VALID_SESSIONS': VALID_SESSIONS}, valid_until,
                   _date(plan['valid_until']))], valid_from, valid_until)


def _earnings_steps(rows: Rows, signal: date, valid_from: date, valid_until: date, *,
                    ref_name: str = 'signal_date', ref_text: str = '信号日'
                    ) -> tuple[Steps, list[str], list[str]]:
    """(steps, invalid, warnings) from the earnings row; only called when that row is available or not needed.

    ``signal`` is the reference session the 10-session warning and "already past" count from: the signal date,
    or an agent-2 version's ``as_of_session`` (named by ``ref_name`` / ``ref_text`` in the step)."""
    warn_until = add_trading_days(signal, EARNINGS_WARN_SESSIONS)
    row = rows.earnings
    flag = _earnings_flag(row, signal, valid_from, valid_until, warn_until)
    invalid = [flag] if flag == 'earnings_in_validity' else []
    warn = [flag] if flag not in ('earnings_in_validity', 'none') else []
    step = _step(
        'earnings_flag', '财报标记',
        f'有效期内有已知财报 → earnings_in_validity（作废）；{ref_text}起 10 个交易日内 → earnings_within_10d；'
        '未知或已过 → earnings_unknown',
        {'earnings_status': (row or {}).get('status'),
         'earnings_date': _date(row['earnings_date']) if row and row.get('earnings_date') else None,
         'valid_from': valid_from, 'valid_until': valid_until, ref_name: signal,
         f'{ref_name}+10': warn_until}, flag)
    return [step], invalid, warn


def _context_step(inp: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    flags, regime_label, sector_state = _context_flags(inp.get('context'))
    return (_step('context_flags', '市场环境提示',
                  '区制缺失 → regime_unavailable；区制为 risk_off → regime_risk_off；行业偏弱 → sector_weak',
                  {'regime': regime_label, 'sector_state': sector_state}, flags), flags)


def _sizing_steps(plan: dict[str, Any], rows: Rows, *, ref: Decimal, ref_name: str, stop: Decimal,
                  entry_high: Decimal) -> tuple[Steps, list[str]]:
    """Per-share risk, risk budget, the three caps, shares and sizing status; (steps, size-invalid reasons).

    ``ref`` is the price the risk per share is measured from: the frozen close for a rule plan, the entry band
    midpoint for own and agent plans. Frozen exposure and sizing parameters come from the plan, not from config.
    """
    inp = plan.get('inputs') or {}
    sizing = plan.get('sizing') or {}
    exposure = inp.get('exposure') or {}
    steps: Steps = []
    per_share_risk = ref - stop
    steps.append(_step('per_share_risk', '每股风险', f'{ref_name} − stop', {ref_name: ref, 'stop': stop},
                       per_share_risk, _opt_dec(sizing.get('per_share_risk'))))
    if rows.snapshot is None:
        steps.append(_step('risk_amount', '风险预算', '无资金快照，不定仓', {}, None,
                           _opt_dec(sizing.get('risk_amount'))))
        steps.append(_step('shares', '股数', '无资金快照，不定仓', {}, None, _opt_int(sizing.get('shares'))))
        steps.append(_step('applied_cap', '生效上限', '无资金快照，不定仓', {}, None, sizing.get('applied_cap')))
        steps.append(_step('sizing.status', '定仓状态', '无资金快照 → no_equity', {}, 'no_equity',
                           sizing.get('status')))
        return steps, []
    equity = _dec(rows.snapshot['equity'])
    risk_pct, single_cap, total_cap = (_dec(sizing[k]) for k in ('risk_pct', 'single_cap', 'total_cap'))
    ticker_value, total_value = _dec(exposure['ticker_value']), _dec(exposure['total_value'])
    source_label = '账本权益 equity' if sizing.get('equity_source') == 'ledger' else '资金快照 equity'
    steps.append(_step('equity', '资金', source_label, {'snapshot_as_of': rows.snapshot.get('as_of')},
                       equity, _opt_dec(sizing.get('equity')), source='frozen'))
    steps.append(_step('ticker_value', '该票市值', '生成时冻结，不重算', {}, ticker_value, source='frozen'))
    steps.append(_step('total_value', '总持仓市值', '生成时冻结，不重算', {}, total_value, source='frozen'))
    risk_amount = equity * risk_pct
    steps.append(_step('risk_amount', '风险预算', 'equity × risk_pct', {'equity': equity, 'risk_pct': risk_pct},
                       risk_amount, _opt_dec(sizing.get('risk_amount'))))
    cap_risk = _floor_div(risk_amount, per_share_risk)
    cap_single = _floor_div(equity * single_cap - ticker_value, entry_high)
    cap_total = _floor_div(equity * total_cap - total_value, entry_high)
    steps.append(_step('cap_risk', '风险上限股数', '⌊risk_amount / per_share_risk⌋，向下取整',
                       {'risk_amount': risk_amount, 'per_share_risk': per_share_risk}, cap_risk))
    steps.append(_step('cap_single', '单票上限股数',
                       '⌊(equity × single_cap − ticker_value) / entry_high⌋，向下取整',
                       {'equity': equity, 'single_cap': single_cap, 'ticker_value': ticker_value,
                        'entry_high': entry_high}, cap_single))
    steps.append(_step('cap_total', '总仓上限股数',
                       '⌊(equity × total_cap − total_value) / entry_high⌋，向下取整',
                       {'equity': equity, 'total_cap': total_cap, 'total_value': total_value,
                        'entry_high': entry_high}, cap_total))
    caps = (('risk', cap_risk), ('single', cap_single), ('total', cap_total))
    shares: int | None = None
    applied_cap: str | None = None
    if all(value is not None for _, value in caps):
        applied_cap, shares = min(caps, key=lambda cap: cap[1])   # a tie keeps the earlier cap
    cap_subs = {'cap_risk': cap_risk, 'cap_single': cap_single, 'cap_total': cap_total}
    steps.append(_step('shares', '股数', 'min(cap_risk, cap_single, cap_total)', cap_subs, shares,
                       _opt_int(sizing.get('shares'))))
    steps.append(_step('applied_cap', '生效上限', 'cap_risk / cap_single / cap_total 中取到最小值的一个'
                                                  '（并列取靠前者）', cap_subs, applied_cap,
                       sizing.get('applied_cap')))
    status = (None if shares is None else 'no_room' if shares <= 0
              else 'sized' if applied_cap == 'risk' else 'capped')
    steps.append(_step('sizing.status', '定仓状态', 'shares ≤ 0 → no_room；风险上限生效 → sized；否则 → capped',
                       {'shares': shares, 'applied_cap': applied_cap}, status, sizing.get('status')))
    return steps, ['no_room'] if shares is not None and shares <= 0 else []


def _verdict_steps(plan: dict[str, Any], *, earn_invalid: list[str], earn_warn: list[str],
                   size_invalid: list[str], context_flags: list[str], own: bool = False) -> Steps:
    """Invalid reasons, warnings and status. An own plan is never invalid: the reasons become warnings."""
    if own:
        invalid: list[str] = []
        warnings = [*earn_warn, *earn_invalid, *size_invalid, *context_flags]
        invalid_step = _step('invalid_reasons', '作废原因', '自定计划不作废，作废原因降级为提示', {}, invalid,
                             plan.get('invalid_reasons') or [])
        warnings_step = _step('warnings', '提示', '财报提示 + 降级的作废原因（财报 / no_room）+ 区制 / 行业提示',
                              {'earnings': [*earn_warn, *earn_invalid], 'sizing': size_invalid,
                               'context': context_flags}, warnings, plan.get('warnings') or [])
        status_step = _step('status', '计划状态', '自定计划恒为 ok', {}, 'ok', plan.get('status'))
        return [invalid_step, warnings_step, status_step]
    invalid = [*earn_invalid, *size_invalid]
    warnings = [*earn_warn, *context_flags]
    return [_step('invalid_reasons', '作废原因', '财报作废 + no_room', {'earnings': earn_invalid, 'sizing': size_invalid},
                  invalid, plan.get('invalid_reasons') or []),
            _step('warnings', '提示', '财报提示 + 区制 / 行业提示', {'earnings': earn_warn, 'context': context_flags},
                  warnings, plan.get('warnings') or []),
            _step('status', '计划状态', '有作废原因 → invalid；否则 → ok', {'invalid_reasons': invalid},
                  'invalid' if invalid else 'ok', plan.get('status'))]


def _order_check(levels: dict[str, Decimal]) -> list[dict[str, Any]]:
    ordered = levels['stop'] < levels['entry_low'] <= levels['entry_high'] < levels['target1']
    return [{'key': 'levels_ordered', 'label': '价位顺序', 'formula': 'stop < entry_low ≤ entry_high < target1',
             'substitutions': {k: _fmt(v) for k, v in levels.items()}, 'holds': ordered}]


# ---------------------------------------------------------------- plan-1 rule plan

def _rule_missing(plan: dict[str, Any], rows: Rows) -> list[str]:
    return ([] if _bars_complete(plan, rows, exact=True) else ['bar_ids']) + _frozen_missing(plan, rows)


def _earnings_flag(row: dict[str, Any] | None, signal: date, valid_from: date, valid_until: date,
                   warn_until: date) -> str:
    if row is None or row.get('status') != 'known' or not row.get('earnings_date'):
        return 'earnings_unknown'
    earnings_date = _date(row['earnings_date'])
    if valid_from <= earnings_date <= valid_until:
        return 'earnings_in_validity'
    if earnings_date < signal:
        return 'earnings_unknown'
    if earnings_date <= warn_until:
        return 'earnings_within_10d'
    return 'none'


def _context_flags(ctx: dict[str, Any] | None) -> tuple[list[str], str, str]:
    """(flags, regime label shown, sector state shown) from the context frozen in the plan."""
    if not ctx:
        return [], _NONE_TEXT, _NONE_TEXT
    flags: list[str] = []
    regime = ctx.get('regime')
    if regime is None:
        flags.append('regime_unavailable')
    elif regime.get('label') == 'risk_off':
        flags.append('regime_risk_off')
    sector = ctx.get('sector')
    if sector and sector.get('state') == 'weak':
        flags.append('sector_weak')
    return (flags, (regime or {}).get('label') or _NONE_TEXT, (sector or {}).get('state') or _NONE_TEXT)


def _derive_rule(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    """Steps, checks and missing items for a ``plan-1`` rule plan."""
    missing = _rule_missing(plan, rows)
    if 'bar_ids' in missing:
        return [], [], missing   # no bars, no ATR: nothing downstream can be derived
    inp = plan.get('inputs') or {}
    bars = rows.bars or []
    snapshot_ok, earnings_ok = _snapshot_ok(missing), _earnings_ok(missing)
    signal = _date(plan['signal_date'])

    steps, atr = _atr_steps(bars, _opt_dec(plan.get('atr')))
    ref_step, ref_price = _ref_price_step(plan, bars)
    steps.append(ref_step)

    levels = {
        'entry_low': _quantize(ref_price - ENTRY_ATR * atr, _CENT),
        'entry_high': _quantize(ref_price + ENTRY_ATR * atr, _CENT),
        'stop': _quantize(ref_price - STOP_ATR * atr, _CENT),
        'target1': _quantize(ref_price + TARGET_ATR * atr, _CENT),
    }
    level_specs = (
        ('entry_low', '− 0.5 × atr', 'ENTRY_ATR', ENTRY_ATR),
        ('entry_high', '+ 0.5 × atr', 'ENTRY_ATR', ENTRY_ATR),
        ('stop', '− 2 × atr', 'STOP_ATR', STOP_ATR),
        ('target1', '+ 4 × atr', 'TARGET_ATR', TARGET_ATR),
    )
    for key, formula, const_name, const in level_specs:
        steps.append(_step(key, LEVEL_LABELS[key], f'ref_price {formula}，四舍五入到分',
                           {'ref_price': ref_price, const_name: const, 'atr': atr}, levels[key],
                           _opt_dec(plan.get(key))))
    steps.append(_trail_step(plan))
    validity, valid_from, valid_until = _validity_steps(plan, signal)
    steps += validity

    earn_invalid: list[str] = []
    earn_warn: list[str] = []
    if earnings_ok:
        earn_steps, earn_invalid, earn_warn = _earnings_steps(rows, signal, valid_from, valid_until)
        steps += earn_steps
    context_step, context_flags = _context_step(inp)
    steps.append(context_step)

    size_invalid: list[str] = []
    if snapshot_ok:
        sizing_steps, size_invalid = _sizing_steps(plan, rows, ref=ref_price, ref_name='ref_price',
                                                   stop=levels['stop'], entry_high=levels['entry_high'])
        steps += sizing_steps
    if snapshot_ok and earnings_ok:
        steps += _verdict_steps(plan, earn_invalid=earn_invalid, earn_warn=earn_warn, size_invalid=size_invalid,
                                context_flags=context_flags)
    return steps, _order_check(levels), missing


# ---------------------------------------------------------------- own and agent plans

def _variant_missing(plan: dict[str, Any], rows: Rows) -> list[str]:
    return ([] if _bars_complete(plan, rows, exact=False) else ['bar_ids']) + _frozen_missing(plan, rows)


def _fallback_atr_step(plan: dict[str, Any], atr: Decimal, source: str, formula: str) -> dict[str, Any]:
    """ATR when fewer than 15 bars were frozen: not computed from bars, so the source says where it came from."""
    return _step('atr', 'ATR14', formula, {}, atr, _opt_dec(plan.get('atr')), source=source)


def _derive_from_levels(plan: dict[str, Any], rows: Rows, missing: list[str], *, level_steps: Steps,
                        levels: dict[str, Decimal], fallback_atr: tuple[Decimal, str, str], agent: bool,
                        validity: tuple[Steps, date, date] | None = None,
                        earnings_ref: tuple[date, str, str] | None = None
                        ) -> tuple[Steps, list[dict], list[str]]:
    """Steps shared by own and agent plans: the four levels are inputs, everything else follows from them.

    Risk per share is measured from the entry band midpoint, not from the signal close. ``validity`` and
    ``earnings_ref`` (reference session, its key and its wording) replace the signal-date window for agent-2.
    """
    inp = plan.get('inputs') or {}
    bars = rows.bars or []
    snapshot_ok, earnings_ok = _snapshot_ok(missing), _earnings_ok(missing)
    signal = _date(plan['signal_date'])

    if len(bars) >= MIN_BARS:
        steps, atr = _atr_steps(bars, _opt_dec(plan.get('atr')))
    else:
        atr, source, formula = fallback_atr
        steps = [_fallback_atr_step(plan, atr, source, formula)]
    if agent and atr == 0:
        steps[-1] = {**steps[-1], 'formula': f"{steps[-1]['formula']}；ATR 为 0，{_BREAKEVEN_NOTE}"}
    ref_step, _ref_price = _ref_price_step(plan, bars)
    steps.append(ref_step)
    steps += level_steps
    steps.append(_trail_step(plan))
    validity_steps, valid_from, valid_until = validity if validity is not None else _validity_steps(plan, signal)
    steps += validity_steps

    earn_invalid: list[str] = []
    earn_warn: list[str] = []
    if earnings_ok:
        ref, ref_name, ref_text = earnings_ref if earnings_ref is not None else (signal, 'signal_date', '信号日')
        earn_steps, earn_invalid, earn_warn = _earnings_steps(rows, ref, valid_from, valid_until,
                                                              ref_name=ref_name, ref_text=ref_text)
        steps += earn_steps
    context_step, context_flags = _context_step(inp)
    steps.append(context_step)

    size_invalid: list[str] = []
    if snapshot_ok:
        entry_mid = (levels['entry_low'] + levels['entry_high']) / 2
        steps.append(_step('entry_mid', '入场区间中点', '(entry_low + entry_high) / 2',
                           {'entry_low': levels['entry_low'], 'entry_high': levels['entry_high']}, entry_mid))
        sizing_steps, size_invalid = _sizing_steps(plan, rows, ref=entry_mid, ref_name='entry_mid',
                                                   stop=levels['stop'], entry_high=levels['entry_high'])
        steps += sizing_steps
    if snapshot_ok and earnings_ok:
        steps += _verdict_steps(plan, earn_invalid=earn_invalid, earn_warn=earn_warn, size_invalid=size_invalid,
                                context_flags=context_flags, own=not agent)
    return steps, _order_check(levels), missing


def _stored_levels(plan: dict[str, Any]) -> dict[str, Decimal]:
    return {key: _dec(plan[key]) for key in LEVEL_LABELS}


def _derive_own(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    """Own plan: the user's four levels are not recomputed; ATR, sizing and verdict are."""
    missing = _variant_missing(plan, rows)
    if 'bar_ids' in missing:
        return [], [], missing
    rule_atr = _opt_dec((rows.rule_plan or {}).get('atr'))
    if len(rows.bars or []) < MIN_BARS and plan.get('based_on_id') is not None and rows.rule_plan is None:
        missing.append(f"trade_plans:{plan['based_on_id']}")   # the ATR fallback would have come from this row
    fallback = ((rule_atr, '取规则计划', '日线不足 15 根，取规则计划的 ATR') if rule_atr is not None
                else (Decimal(0), '无 ATR', '日线不足 15 根且无规则计划 ATR，取 0'))
    levels = _stored_levels(plan)
    level_steps = [_step(key, LEVEL_LABELS[key], '你的输入，不重算', {}, levels[key], source='user_input')
                   for key in LEVEL_LABELS]
    return _derive_from_levels(plan, rows, missing, level_steps=level_steps, levels=levels,
                               fallback_atr=fallback, agent=False)


def _derive_agent(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    """Agent plan: each stored level must equal ``agent_runs.output.levels.<key>.value``; the rest as for own."""
    missing = _variant_missing(plan, rows)
    if 'bar_ids' in missing:
        return [], [], missing
    levels, level_steps = _agent_levels(plan, rows, missing)
    return _derive_from_levels(plan, rows, missing, level_steps=level_steps, levels=levels,
                               fallback_atr=(Decimal(0), '无 ATR', '日线不足 15 根，ATR 取 0'), agent=True)


def _agent_levels(plan: dict[str, Any], rows: Rows, missing: list[str]) -> tuple[dict[str, Decimal], Steps]:
    """(stored levels, level steps): each stored level checked against the agent run's cited value."""
    run_id = (plan.get('inputs') or {}).get('agent_run_id')
    cited: dict[str, Any] = {}
    if rows.agent_run is None:
        missing.append(f'agent_runs:{run_id}')
    else:
        output = rows.agent_run.get('output')
        raw = output.get('levels') if isinstance(output, dict) else None
        for key in LEVEL_LABELS:
            item = raw.get(key) if isinstance(raw, dict) else None
            if isinstance(item, dict) and item.get('value') is not None:
                cited[key] = item
            else:
                missing.append(f'agent_runs.output.levels.{key}')
    levels = _stored_levels(plan)
    level_steps: Steps = []
    for key in LEVEL_LABELS:
        item = cited.get(key)
        if item is None:   # nothing cited to check against: show the stored level, incomplete says why
            level_steps.append(_step(key, LEVEL_LABELS[key], 'Agent 引用，未取到 agent_runs 的价位', {}, levels[key],
                                     source='agent_output', refs=[]))
            continue
        level_steps.append(_step(
            key, LEVEL_LABELS[key], f'已存价位须等于 agent_runs.output.levels.{key}.value',
            {f'output.levels.{key}.value': _dec(item['value'])}, _dec(item['value']), levels[key],
            source='agent_output', refs=[str(r) for r in item.get('refs') or []]))
    return levels, level_steps


def _version_steps(plan: dict[str, Any], rows: Rows, missing: list[str]) -> tuple[Steps, int, date]:
    """agent-2: the version number and generation session the plan froze, checked against its
    ``agent_plan_versions`` row; (steps, version_no, as_of_session). A missing row or input is listed."""
    inp = plan.get('inputs') or {}
    signal = _date(plan['signal_date'])
    version_no = _opt_int(inp.get('version_no'))
    as_of = _date(inp['as_of_session']) if inp.get('as_of_session') else None
    if version_no is None:
        missing.append('inputs.version_no')
    if as_of is None:
        missing.append('inputs.as_of_session')
    version = rows.version
    if version is None:
        missing.append(f"agent_plan_versions:{plan.get('id')}")
    stored_no = _opt_int(version.get('version_no')) if version is not None else _NO_STORED
    stored_as_of = _date(version['as_of_session']) if version is not None else _NO_STORED
    version_no = 0 if version_no is None else version_no
    as_of = signal if as_of is None else as_of
    return ([_step('version_no', '版本号', 'inputs.version_no，须等于 agent_plan_versions.version_no',
                   {'inputs.version_no': version_no}, version_no, stored_no),
             _step('as_of_session', '本版生成交易日', 'inputs.as_of_session，须等于 agent_plan_versions.as_of_session',
                   {'inputs.as_of_session': as_of}, as_of, stored_as_of)],
            version_no, as_of)


def _chain_validity_steps(plan: dict[str, Any], version_no: int, as_of: date,
                          rows: Rows | None = None) -> tuple[Steps, date, date]:
    """agent-2 validity (D4): version 0 from the signal date, a revision from its generation session;
    an entry-phase chain (``inputs.chain_entry_cap`` set) caps ``valid_until`` at the signal date + 10. With
    ``rows`` (agent-3, D6) the cap is the later of that and the ``entry_cap`` of every ``chain_retriggers`` row
    of the chain written by the version's ``cutoff_at``."""
    signal = _date(plan['signal_date'])
    inp = plan.get('inputs') or {}
    if version_no == 0:
        start, start_name, start_text = signal, 'signal_date', '信号日'
    else:
        start, start_name, start_text = as_of, 'as_of_session', '本版生成交易日'
    valid_from = add_trading_days(start, 1)
    base_until = add_trading_days(start, VALID_SESSIONS)
    steps = [_step('valid_from', '有效期起', f'{start_text}后第 1 个交易日', {start_name: start}, valid_from,
                   _date(plan['valid_from']))]
    stored_cap = inp.get('chain_entry_cap')
    if stored_cap is None:      # holding phase: no cap
        steps.append(_step('valid_until', '有效期止', f'{start_text}后第 3 个交易日（持仓期不设上限）',
                           {start_name: start, 'VALID_SESSIONS': VALID_SESSIONS}, base_until,
                           _date(plan['valid_until'])))
        return steps, valid_from, base_until
    cap = add_trading_days(signal, CHAIN_ENTRY_SESSIONS)
    if rows is None:
        steps.append(_step('chain_entry_cap', '入场期链上限', '信号日后第 10 个交易日',
                           {'signal_date': signal, 'CHAIN_ENTRY_SESSIONS': CHAIN_ENTRY_SESSIONS}, cap,
                           _date(stored_cap)))
    else:
        cutoff = (rows.version or {}).get('cutoff_at')
        triggers = [r for r in rows.retriggers if r.get('entry_cap') is not None
                    and (cutoff is None or r['created_at'] <= cutoff)]
        later = max([cap, *(_date(r['entry_cap']) for r in triggers)])
        steps.append(_step('chain_entry_cap', '入场期链上限',
                           '信号日后第 10 个交易日，与最近一次触发后第 10 个交易日取较大者',
                           {'signal_date': signal, 'CHAIN_ENTRY_SESSIONS': CHAIN_ENTRY_SESSIONS,
                            'chain_retriggers.entry_cap': [_date(r['entry_cap']) for r in triggers]}, later,
                           _date(stored_cap)))
        cap = later
    valid_until = min(base_until, cap)
    steps.append(_step('valid_until', '有效期止', f'min({start_text}后第 3 个交易日, chain_entry_cap)',
                       {f'{start_name}+3': base_until, 'chain_entry_cap': cap}, valid_until,
                       _date(plan['valid_until'])))
    return steps, valid_from, valid_until


def _derive_agent_chain(plan: dict[str, Any], rows: Rows, *, v3: bool) -> tuple[list[dict], list[dict], list[str]]:
    """agent-2 / agent-3 version plan: levels as agent-1, the version row checked, validity from as_of_session and the
    chain cap, earnings flagged relative to as_of_session (the session the version was generated on). agent-3 adds
    the two execution fields (against the run's output) and reads the cap from the chain's retrigger rows."""
    missing = _variant_missing(plan, rows)
    version_steps, version_no, as_of = _version_steps(plan, rows, missing)
    if 'bar_ids' in missing:
        return [], [], missing
    levels, level_steps = _agent_levels(plan, rows, missing)
    steps, checks, missing = _derive_from_levels(
        plan, rows, missing, level_steps=level_steps, levels=levels,
        fallback_atr=(Decimal(0), '无 ATR', '日线不足 15 根，ATR 取 0'), agent=True,
        validity=_chain_validity_steps(plan, version_no, as_of, rows if v3 else None),
        earnings_ref=(as_of, 'as_of_session', '本版生成交易日'))
    extra: Steps = _agent_exec_steps(plan, rows, missing) if v3 else []
    return version_steps + steps + extra, checks, missing


def _agent_exec_steps(plan: dict[str, Any], rows: Rows, missing: list[str]) -> Steps:
    """agent-3: the stored ``entry_type`` / ``max_hold_sessions`` must equal what the agent run's output chose (and
    lie in the allowed set / range, which the run's own check enforced)."""
    output = (rows.agent_run or {}).get('output') if rows.agent_run is not None else None
    if not isinstance(output, dict):
        return []
    steps: Steps = []
    cited_type, cited_hold = output.get('entry_type'), output.get('max_hold_sessions')
    if cited_type not in ENTRY_TYPES:
        missing.append('agent_runs.output.entry_type')
    else:
        steps.append(_step('entry_type', '入场类型', '已存入场类型须等于 agent_runs.output.entry_type',
                           {'output.entry_type': cited_type}, cited_type, plan.get('entry_type'),
                           source='agent_output'))
    if isinstance(cited_hold, bool) or not isinstance(cited_hold, int):
        missing.append('agent_runs.output.max_hold_sessions')
    else:
        steps.append(_step('max_hold_sessions', '最长持有', '已存最长持有须等于 agent_runs.output.max_hold_sessions',
                           {'output.max_hold_sessions': cited_hold}, cited_hold,
                           _opt_int(plan.get('max_hold_sessions')), source='agent_output'))
    return steps


def _derive_agent2(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    return _derive_agent_chain(plan, rows, v3=False)


def _derive_agent3(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    return _derive_agent_chain(plan, rows, v3=True)


def _derive_own2(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    """``plan-2`` own plan (written with a fill, spec §6.1): the user's levels as for ``plan-1``; the validity is the
    fill day alone (the day the plan froze as ``valid_from``), the entry type is ``zone_limit`` and the longest hold
    is the user's 3–30 sessions (default 10)."""
    fill = _date(plan['valid_from'])
    validity = ([_step('valid_from', '有效期起', '成交当日（自定计划只在成交日有效）', {'fill_session': fill}, fill,
                       _date(plan['valid_from'])),
                 _step('valid_until', '有效期止', '成交当日（自定计划只在成交日有效）', {'fill_session': fill}, fill,
                       _date(plan['valid_until']))], fill, fill)
    missing = _variant_missing(plan, rows)
    if 'bar_ids' in missing:
        return [], [], missing
    rule_atr = _opt_dec((rows.rule_plan or {}).get('atr'))
    if len(rows.bars or []) < MIN_BARS and plan.get('based_on_id') is not None and rows.rule_plan is None:
        missing.append(f"trade_plans:{plan['based_on_id']}")
    fallback = ((rule_atr, '取规则计划', '日线不足 15 根，取规则计划的 ATR') if rule_atr is not None
                else (Decimal(0), '无 ATR', '日线不足 15 根且无规则计划 ATR，取 0'))
    levels = _stored_levels(plan)
    level_steps = [_step(key, LEVEL_LABELS[key], '你的输入，不重算', {}, levels[key], source='user_input')
                   for key in LEVEL_LABELS]
    steps, checks, missing = _derive_from_levels(plan, rows, missing, level_steps=level_steps, levels=levels,
                                                 fallback_atr=fallback, agent=False, validity=validity)
    hold = _opt_int(plan.get('max_hold_sessions'))
    in_range = hold is not None and MAX_HOLD_RANGE[0] <= hold <= MAX_HOLD_RANGE[1]
    steps = steps + [
        _step('entry_type', '入场类型', '自定计划恒用 zone_limit', {'RULE_ENTRY_TYPE': RULE_ENTRY_TYPE},
              RULE_ENTRY_TYPE, plan.get('entry_type')),
        _step('max_hold_sessions', '最长持有', f'你的输入，须在 {MAX_HOLD_RANGE[0]}–{MAX_HOLD_RANGE[1]} 个交易日内'
              f'（缺省 {RULE_MAX_HOLD_SESSIONS}）', {'range': list(MAX_HOLD_RANGE)}, hold if in_range else RULE_MAX_HOLD_SESSIONS,
              hold, source='user_input')]
    return steps, checks, missing


def _derive_rule2(plan: dict[str, Any], rows: Rows) -> tuple[list[dict], list[dict], list[str]]:
    """``plan-2``: the ``plan-1`` derivation word for word, plus the two execution fields against the rule constants."""
    steps, checks, missing = _derive_rule(plan, rows)
    steps = steps + [
        _step('entry_type', '入场类型', 'plan-2 的规则基线恒用 zone_limit', {'RULE_ENTRY_TYPE': RULE_ENTRY_TYPE},
              RULE_ENTRY_TYPE, plan.get('entry_type')),
        _step('max_hold_sessions', '最长持有', 'plan-2 的规则基线恒为 RULE_MAX_HOLD_SESSIONS 个交易日',
              {'RULE_MAX_HOLD_SESSIONS': RULE_MAX_HOLD_SESSIONS}, RULE_MAX_HOLD_SESSIONS,
              _opt_int(plan.get('max_hold_sessions')))]
    return steps, checks, missing


# ---------------------------------------------------------------- trigger section

class Clause(NamedTuple):
    """One condition sentence of a rule: its text, the check over frozen values, and the operands it reads.

    ``check(values, thresholds)`` returns True (holds), False (violated) or None (a needed value was not frozen).
    ``operands`` are ``values.<key>`` / ``thresholds.<key>`` names, shown with the frozen value next to the result.
    """

    text: str
    check: Callable[[dict[str, Any], dict[str, Any]], bool | None]
    operands: tuple[str, ...] = ()


CONDITION_VERSIONS = ('1',)   # rules.RULE_VERSION values these clauses mirror
FILING_FORMS = ('10-Q', '10-K', '8-K')


def _f(source: dict[str, Any], key: str) -> float | None:
    """A frozen number as float (the candidate stores JSON floats, as ``rules.py`` compared them)."""
    value = source.get(key)
    return None if value is None or isinstance(value, bool) else float(value)


def _cmp(op: Callable[[float, float], bool], left: float | None, right: float | None) -> bool | None:
    return None if left is None or right is None else op(left, right)


def _times(factor: float | None, value: float | None) -> float | None:
    return None if factor is None or value is None else factor * value


def _neg(value: float | None) -> float | None:
    return None if value is None else -value


def _all(*results: bool | None) -> bool | None:
    if any(r is False for r in results):
        return False
    return None if any(r is None for r in results) else True


def _any(*results: bool | None) -> bool | None:
    if any(r is True for r in results):
        return True
    return None if any(r is None for r in results) else False


def _never(_values: dict[str, Any], _thresholds: dict[str, Any]) -> None:
    return None


def _forms_ok(values: dict[str, Any], _thresholds: dict[str, Any]) -> bool | None:
    forms = values.get('forms')
    if not isinstance(forms, (list, tuple)):
        return None
    return bool(forms) and all(form in FILING_FORMS for form in forms)


_GAP_OPERANDS = ('values.gap', 'values.open', 'values.prev_close', 'thresholds.gap')

# Each rule's clauses mirror the decision expression of its function in rules.py one-to-one.
CONDITIONS: dict[str, tuple[Clause, ...]] = {
    'trend_reclaim': (
        Clause('close > ma50 > ma200',
               lambda v, t: _all(_cmp(gt, _f(v, 'close'), _f(v, 'ma50')), _cmp(gt, _f(v, 'ma50'), _f(v, 'ma200'))),
               ('values.close', 'values.ma50', 'values.ma200')),
        Clause('close > ma20', lambda v, t: _cmp(gt, _f(v, 'close'), _f(v, 'ma20')), ('values.close', 'values.ma20')),
        Clause('此前连续 3 日收于 MA20 之下', _never, ('thresholds.days_below_ma20',)),   # daily MA20 not frozen
    ),
    'volume_breakout': (
        Clause('close > prior_high', lambda v, t: _cmp(gt, _f(v, 'close'), _f(v, 'prior_high')),
               ('values.close', 'values.prior_high')),
        Clause('volume ≥ 1.5 × avg_volume_50',
               lambda v, t: _cmp(ge, _f(v, 'volume'), _times(_f(t, 'volume_multiple'), _f(v, 'avg_volume_50'))),
               ('values.volume', 'values.avg_volume_50', 'thresholds.volume_multiple')),
        Clause('close > ma50', lambda v, t: _cmp(gt, _f(v, 'close'), _f(v, 'ma50')), ('values.close', 'values.ma50')),
    ),
    'earnings_gap_up': (
        Clause('gap = open / prev_close − 1 ≥ 0.03', lambda v, t: _cmp(ge, _f(v, 'gap'), _f(t, 'gap')),
               _GAP_OPERANDS),
        Clause('close ≥ open', lambda v, t: _cmp(ge, _f(v, 'close'), _f(v, 'open')), ('values.close', 'values.open')),
    ),
    'trend_break': (
        Clause('此前连续 10 日收于 MA50 之上', _never, ('thresholds.days_above_ma50',)),   # daily MA50 not frozen
        Clause('close < ma50', lambda v, t: _cmp(lt, _f(v, 'close'), _f(v, 'ma50')), ('values.close', 'values.ma50')),
    ),
    'earnings_gap_down': (
        Clause('gap = open / prev_close − 1 ≤ −0.03', lambda v, t: _cmp(le, _f(v, 'gap'), _neg(_f(t, 'gap'))),
               _GAP_OPERANDS),
        Clause('close ≤ open', lambda v, t: _cmp(le, _f(v, 'close'), _f(v, 'open')), ('values.close', 'values.open')),
    ),
    'position_drawdown': (
        Clause('from_cost ≤ −0.08 或 from_high ≤ −0.12',
               lambda v, t: _any(_cmp(le, _f(v, 'from_cost'), _neg(_f(t, 'cost_stop'))),
                                 _cmp(le, _f(v, 'from_high'), _neg(_f(t, 'trailing_stop')))),
               ('values.from_cost', 'values.from_high', 'thresholds.cost_stop', 'thresholds.trailing_stop')),
    ),
    'abnormal_move': (
        Clause('return ≠ 0', lambda v, t: _cmp(ne, _f(v, 'return'), 0.0), ('values.return',)),
        Clause('|return| ≥ 2.5 × stdev_20',
               lambda v, t: _cmp(ge, None if _f(v, 'return') is None else abs(_f(v, 'return')),
                                 _times(_f(t, 'sigma'), _f(v, 'stdev_20'))),
               ('values.return', 'values.stdev_20', 'thresholds.sigma')),
    ),
    'plan_stop': (
        Clause('low ≤ stop', lambda v, t: _cmp(le, _f(v, 'low'), _f(v, 'stop')), ('values.low', 'values.stop')),
    ),
    'plan_target': (
        Clause('high ≥ target1', lambda v, t: _cmp(ge, _f(v, 'high'), _f(v, 'target1')),
               ('values.high', 'values.target1')),
        Clause('目标一此前未触及（t1_reached 为否）', _never),   # plan state not frozen in the candidate
    ),
    'new_filing': (
        Clause('forms 非空，且均属 10-Q / 10-K / 8-K', _forms_ok, ('values.forms',)),
        Clause('公告对用户是新的（filing_session 等于信号日）', _never),   # filing times not frozen
    ),
}

UNKNOWN_RULE_VERSION = 'unknown_rule_version'


def _operand(name: str, values: dict[str, Any], thresholds: dict[str, Any]) -> Any:
    source, _, key = name.partition('.')
    return _jsonable((values if source == 'values' else thresholds).get(key))


def _clause_result(clause: Clause, values: dict[str, Any], thresholds: dict[str, Any]) -> str:
    try:
        verdict = clause.check(values, thresholds)
    except (TypeError, ValueError):   # a frozen value that is not a number cannot be reviewed
        verdict = None
    return 'not_frozen' if verdict is None else 'holds' if verdict else 'violated'


def _trigger_item(candidate: dict[str, Any]) -> dict[str, Any]:
    rule_id, version = candidate.get('rule_id'), str(candidate.get('rule_version'))
    inputs = candidate.get('inputs') or {}
    values, thresholds = inputs.get('values') or {}, inputs.get('thresholds') or {}
    clauses = CONDITIONS.get(rule_id, ())
    item: dict[str, Any] = {'candidate_id': _ser(candidate.get('id')), 'rule_id': rule_id,
                            'rule_version': version, 'action': candidate.get('action')}
    if version in CONDITION_VERSIONS and rule_id in CONDITIONS:
        item['clauses'] = [{'text': c.text,
                            'operands': {name: _operand(name, values, thresholds) for name in c.operands},
                            'result': _clause_result(c, values, thresholds)} for c in clauses]
    else:   # nothing registered for this version: show what is known, review nothing
        item['clauses'] = [{'text': c.text, 'operands': {}, 'result': 'not_frozen', 'note': UNKNOWN_RULE_VERSION}
                           for c in clauses]
        item['note'] = UNKNOWN_RULE_VERSION
    return item


def derive_trigger(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per candidate, each condition clause checked against the values the candidate froze.

    Only the stored values are reviewed against the stored thresholds; nothing is recomputed from daily bars.
    """
    return [_trigger_item(c) for c in candidates]


# ---------------------------------------------------------------- dispatch and verdict

# (plan_version, kind) -> derivation function.
_DERIVERS: dict[tuple[str, str], Callable[[dict[str, Any], Rows], tuple[list[dict], list[dict], list[str]]]] = {
    ('plan-1', 'rule'): _derive_rule,
    ('plan-2', 'rule'): _derive_rule2,
    ('plan-1', 'own'): _derive_own,
    ('plan-2', 'own'): _derive_own2,
    ('agent-1', 'agent'): _derive_agent,
    ('agent-2', 'agent'): _derive_agent2,
    ('agent-3', 'agent'): _derive_agent3,
}


def consistency(steps: list[dict[str, Any]], trigger: list[dict[str, Any]], missing: list[str]) -> dict[str, Any]:
    """consistent / mismatch / incomplete; missing rows or an unregistered version outrank a mismatch.

    ``trigger`` is the trigger section as ``derive_trigger`` returns it (candidates with nested ``clauses``);
    a violated clause is a mismatch keyed ``trigger:<rule_id>``. A malformed item raises ``KeyError`` instead
    of being skipped, so a violated clause cannot be ignored by accident.
    """
    mismatches = [{'key': s['key'], 'result': s['result'], 'stored': s['stored']}
                  for s in steps if s['consistent'] is False]
    for item in trigger:
        violated = [c['text'] for c in item['clauses'] if c['result'] == 'violated']
        if violated:
            mismatches.append({'key': f"trigger:{item['rule_id']}", 'result': 'violated',
                               'stored': '；'.join(violated)})
    missing = sorted(set(missing))
    status = 'incomplete' if missing else 'mismatch' if mismatches else 'consistent'
    return {'status': status, 'mismatches': mismatches, 'missing': missing}


def derive_plan(plan: dict[str, Any], rows: Rows) -> dict[str, Any]:
    """The full derivation of one stored plan against the rows its ``inputs`` point at."""
    version, kind = plan.get('plan_version'), plan.get('kind')
    missing = list(rows.missing)
    steps: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    if version not in REGISTERED_VERSIONS:
        missing.append('unknown_plan_version')
    elif (version, kind) not in _DERIVERS:
        missing.append(f'no_derivation:{kind}')
    else:
        steps, checks, more = _DERIVERS[(version, kind)](plan, rows)
        missing += more
    trigger = derive_trigger(rows.candidates)
    return {
        'derivation_version': DERIVATION_VERSION,
        'plan': {'id': _ser(plan.get('id')), 'ticker': plan.get('ticker'),
                 'signal_date': _ser(_date(plan['signal_date'])) if plan.get('signal_date') else None,
                 'kind': kind, 'plan_version': version, 'status': plan.get('status')},
        'trigger': trigger,
        'inputs': _inputs_view(plan, rows),
        'steps': steps,
        'checks': checks,
        'consistency': consistency(steps, trigger, missing),
        'evidence': EVIDENCE_TEXT,
    }


def derive_unit(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """The trigger section alone, for a unit that has no plan."""
    trigger = derive_trigger(candidates)
    return {'derivation_version': DERIVATION_VERSION, 'trigger': trigger,
            'consistency': consistency([], trigger, []), 'evidence': EVIDENCE_TEXT, 'note': TRIGGER_NOTE}


# ---------------------------------------------------------------- command line (v2.4 §8 第 3 点)
#
# Read-only: every command runs inside ``core_tx.read_only_tx()`` and writes nothing. The pure part above
# stays importable without a database, so the view layer (which imports this module) is imported lazily.

NY = ZoneInfo('America/New_York')
PLAN_KINDS = ('rule', 'own', 'agent')
# effective stage rows per run (the highest attempt of each stage), by stages_version (v2.5 §7.1, v2.7 §9, v2.8 D9)
RUN_STAGE_COUNT = {'stages-1': 7, 'stages-2': 8, 'stages-3': 9, 'stages-4': 10, 'stages-5': 11}


def session_window(session: date) -> tuple[datetime, datetime]:
    """[the session's New York 16:00, the next trading day's New York 16:00) as UTC instants."""
    start = datetime.combine(session, dtime(16, 0), tzinfo=NY)
    end = datetime.combine(add_trading_days(session, 1), dtime(16, 0), tzinfo=NY)
    return start.astimezone(UTC), end.astimezone(UTC)


def _beijing(moment: datetime | None) -> str:
    from investment_assistant.workbench.review_digest import SH      # the workbench's Beijing-time constant

    return moment.astimezone(SH).strftime('%Y-%m-%d %H:%M') if moment is not None else '-'


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)


def _table(headers: list[str], rows: list[list[str]], indent: str = '') -> list[str]:
    """Plain-text table, columns padded to the widest cell (CJK counted double-width)."""
    widths = [max(_width(cell) for cell in column) for column in zip(headers, *rows)]
    return [indent + '  '.join(cell + ' ' * (w - _width(cell)) for cell, w in zip(row, widths)).rstrip()
            for row in [headers, *rows]]


def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'日期格式应为 YYYY-MM-DD：{text}') from None


def _check(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from investment_assistant.workbench import derivation_view, repository, service

    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with core_tx.read_only_tx() as conn:
        for plan in repository.plans_since(conn, args.since):
            if (args.until is not None and plan['signal_date'] > args.until) \
                    or (args.kind is not None and plan['kind'] != args.kind):
                continue
            results.append((plan, derive_plan(plan, derivation_view.load_rows(conn, plan))['consistency']))
    rows = [[str(plan['id']), plan['ticker'], plan['signal_date'].isoformat(), plan['kind'], plan['status'],
             consistency['status'], _beijing(plan['created_at'])] for plan, consistency in results]
    if rows:
        for line in _table(['plan_id', '标的', '信号日', '种类', '状态', '一致性', '生成时间（北京）'], rows):
            out(line)
        for plan, consistency in results:
            tag = f"  {str(plan['id'])[:8]} {plan['ticker']}"
            for item in consistency['mismatches']:
                out(f"{tag} 差异 {item['key']}：推导 {item['result']} ≠ 已存 {item['stored']}")
            for item in consistency['missing']:
                out(f'{tag} 缺失 {item}')
    count = lambda status: sum(c['status'] == status for _, c in results)   # noqa: E731
    failing = sum(p['status'] == 'ok' and c['status'] != 'consistent' for p, c in results)
    out(f"汇总：{len(results)} 份，consistent {count('consistent')}，mismatch {count('mismatch')}，"
        f"incomplete {count('incomplete')}；status=ok 且不一致 {failing} 份 → {'不通过' if failing else '通过'}")
    return 1 if failing else 0


def _stage_derivation(row: dict[str, Any]) -> str:
    found = (row.get('detail') or {}).get('derivation')
    if not found:
        return '-'
    return f"{found['checked']} 份，mismatch {found['mismatch']}，incomplete {found['incomplete']}"


def _attempt_problems(rows: list[dict[str, Any]]) -> list[str]:
    """The attempt guard of ``stages-4`` (v2.8 §4.4): per stage the attempts count 1, 2, … without a gap, and an attempt
    above 1 exists only where the previous one failed or was skipped as ``upstream_failed`` — a completed node never
    runs again. A row without ``attempt`` (before migration 028) is attempt 1."""
    by_stage: dict[str, dict[int, dict[str, Any]]] = {}
    for row in rows:
        by_stage.setdefault(row['stage'], {})[int(row.get('attempt') or 1)] = row
    problems = []
    for stage, attempts in by_stage.items():
        numbers = sorted(attempts)
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(f"{stage} 的 attempt 不连续：{', '.join(str(n) for n in numbers)}")
            continue
        for n in numbers[1:]:
            before = attempts[n - 1]
            if not (before['status'] == 'failed'
                    or (before['status'] == 'skipped' and before.get('reason') == 'upstream_failed')):
                problems.append(f"{stage} 的 attempt {n} 不该存在：上一次是 {before['status']}（已完成的节点不重跑）")
    return problems


def _stages(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    from investment_assistant.workbench import repository, service
    from investment_assistant.workbench import stages as stages_module
    from investment_assistant.workbench import workflow

    start, end = session_window(args.session)
    with core_tx.read_only_tx() as conn:
        ready = repository.stage_runs_ready(conn)
        reports = repository.workbench_reports_between(conn, start, end)
        runs: dict[str, list[dict[str, Any]]] = {}
        for row in repository.stage_runs_for_session(conn, args.session) if ready else []:
            runs.setdefault(row['run_id'], []).append(row)
        no_evidence = []
        for report in reports:
            if report['run_id'] in runs:
                continue
            rows = repository.stage_runs_for_run(conn, report['run_id']) if ready else []
            if rows:
                runs[report['run_id']] = rows
            else:
                no_evidence.append(report)
        if ready and repository.daily_ready(conn):          # migration 028: rows with their attempt
            runs = {run_id: repository.stage_rows_for_run(conn, run_id) for run_id in runs}
        reported = repository.workbench_run_ids(conn, list(runs))
    if not ready:
        out('迁移 025 未执行，暂无阶段证据')
    complete = incomplete = unreported = 0
    for run_id, all_rows in runs.items():
        rows = list(workflow.effective_rows(all_rows).values())
        have = {row['stage'] for row in rows}
        versions = sorted({row['stages_version'] for row in rows})
        version = versions[0] if len(versions) == 1 else None
        expected = stages_module.STAGE_IDS_BY_VERSION.get(version) if version is not None else None
        missing = [stage for stage in expected if stage not in have] if expected is not None else []
        problems = _attempt_problems(all_rows)
        ok = expected is not None and not missing and not problems and len(rows) == RUN_STAGE_COUNT[version]
        complete += ok
        incomplete += not ok
        unreported += run_id not in reported
        first = all_rows[0]
        extra = f'（共 {len(all_rows)} 条记录，含重试）' if len(all_rows) != len(rows) else ''
        out(f"运行 {run_id}  {first['trigger']}  session {first['session']}  {'/'.join(versions)}  "
            f"开始 {_beijing(min(row['started_at'] for row in all_rows))}（北京）  {len(rows)} 行{extra}")
        order = {stage: i for i, stage in enumerate(expected or stages_module.STAGE_IDS)}
        table = [[row['stage'], row['status'], str(row['input_count']), str(row['output_count']),
                  _stage_derivation(row), row['reason'] or ''] for row in sorted(rows, key=lambda r: order.get(r['stage'], 99))]
        for line in _table(['阶段', '状态', '输入', '输出', '推导', '原因'], table, indent='  '):
            out(line)
        if expected is None:
            out(f"  阶段版本无法核对：{', '.join(versions)}")
        if missing:
            out(f"  缺少阶段：{', '.join(missing)}")
        for problem in problems:
            out(f'  {problem}')
        if run_id not in reported:
            out(f'  不在 job_reports：{run_id}')
    for report in no_evidence:
        out(f"缺证据  {report['run_id']}  {report['trigger_source']}  {_beijing(report['started_at'])}（北京）")
    passed = ready and not incomplete and not unreported and not no_evidence
    counts = '，'.join(f'{version} {count} 行' for version, count in RUN_STAGE_COUNT.items())
    out(f"汇总：{len(runs)} 个运行，{complete} 个齐全（{counts}），缺阶段行 {incomplete} 个，"
        f"缺证据 {len(no_evidence)} 个，不在 job_reports {unreported} 个 → {'通过' if passed else '不通过'}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    """``check`` lists plan consistency (exit 1 when a status=ok plan is not consistent); ``stages`` verifies
    the stage evidence of one session (exit 1 on a run without its version's effective rows — 7 for stages-1,
    8 for stages-2, 9 for stages-3, 10 for stages-4 — or whose attempts are not consecutive or rerun a completed node,
    a run missing from job_reports or a report without stage rows). Exit 2 for bad
    arguments or an unavailable database."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.workbench.derivation',
                                     description='计划推导与阶段证据的只读校验。')
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='逐份核对计划与推导是否一致')
    check.add_argument('--since', type=_date_arg, required=True, help='信号日起（含），YYYY-MM-DD')
    check.add_argument('--until', type=_date_arg, help='信号日止（含），YYYY-MM-DD')
    check.add_argument('--kind', choices=PLAN_KINDS, help='只看一种计划')
    stages = commands.add_parser('stages', help='核对一个 session 的阶段证据')
    stages.add_argument('--session', type=_date_arg, required=True, help='交易日，YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        return (_check if args.command == 'check' else _stages)(args, print)
    except Exception as exc:    # noqa: BLE001 - a CLI reports an unavailable database instead of a traceback
        from investment_assistant.workbench import service

        if isinstance(exc, service.Unavailable):
            print(f'数据库不可用：{exc}', file=sys.stderr)
            return 2
        raise


if __name__ == '__main__':
    raise SystemExit(main())
