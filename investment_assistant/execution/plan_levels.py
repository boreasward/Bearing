"""Plan-1 / plan-2 trade plan math: ATR14, rule levels, validity, earnings flags, sizing.

Pure functions; SQL and transactions live elsewhere. All prices come from
price_bars OHLC (split-adjusted, not dividend-adjusted). Levels are rounded to
cents half-up; ref_price and atr keep full precision and are frozen into the
plan at generation. Changing a constant means a new PLAN_VERSION.

`plan-2` (v2.9 spec §5.4) is `plan-1` word for word (levels, validity, sizing, earnings rules) plus the two
execution fields `entry_type = 'zone_limit'` and `max_hold_sessions = 10` and `inputs.entry_rules_version`.
The builders take `version=`: the default stays `plan-1`, so a caller that has not been rewired (Task 3 / 4)
still writes exactly the old rows; passing `PLAN_V2` yields the new shape.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

from investment_assistant.config import WorkbenchConfig
from investment_assistant.core.constants import MIN_BARS_ATR as MIN_BARS
from investment_assistant.derivation.indicators.price import atr14
from investment_assistant.core.versions import (AGENT_VERSION as AGENT_VERSION, PLAN_V1 as PLAN_V1,
                                                PLAN_V2 as PLAN_V2)
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.execution.entry_rules import DEFAULT_ENTRY_TYPE, ENTRY_RULES_VERSION

PLAN_VERSION = PLAN_V2               # v2.9: `stage_rules` writes plan-2 rule baselines, `derivation` checks them
RULE_MAX_HOLD_SESSIONS = 10          # plan-2 rule baseline: exit when still below +1R after this many sessions
MAX_HOLD_DEFAULT = 10                # what a plan without the field (agent-2, plan-1) is read as
MAX_HOLD_RANGE = (3, 30)             # trade_plans.max_hold_sessions CHECK
CONTEXT_VERSION = 'context-2'
ENTRY_ATR = Decimal('0.5')
STOP_ATR = Decimal('2')
TARGET_ATR = Decimal('4')
TRAIL_ATR_MULTIPLE = Decimal('3')
VALID_SESSIONS = 3
EARNINGS_WARN_SESSIONS = 10

CENT = Decimal('0.01')
SIX = Decimal('0.000001')  # price_bars NUMERIC(18,6): freeze ref_price/atr at storable precision
LEVEL_KEYS = ('entry_low', 'entry_high', 'stop', 'target1')


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _freeze(value: Decimal) -> Decimal:
    return value.quantize(SIX, rounding=ROUND_HALF_UP)


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def rule_levels(ref_price: Decimal, atr: Decimal) -> dict[str, Decimal]:
    """Entry band ±0.5 ATR around the reference close, stop 2 ATR below, target1 4 ATR above."""
    return {
        'entry_low': _cent(ref_price - ENTRY_ATR * atr),
        'entry_high': _cent(ref_price + ENTRY_ATR * atr),
        'stop': _cent(ref_price - STOP_ATR * atr),
        'target1': _cent(ref_price + TARGET_ATR * atr),
    }


def levels_ordered(levels: dict[str, Decimal]) -> bool:
    return levels['stop'] < levels['entry_low'] <= levels['entry_high'] < levels['target1']


def validity(signal_date: date) -> tuple[date, date]:
    """The plan may fill from T+1 through T+3, trading days only."""
    return add_trading_days(signal_date, 1), add_trading_days(signal_date, VALID_SESSIONS)


def earnings_flags(row: dict[str, Any] | None, *, signal_date: date, valid_from: date,
                   valid_until: date) -> tuple[list[str], list[str]]:
    """(invalid_reasons, warnings) for the next earnings date known at the cutoff."""
    if row is None or row.get('status') != 'known' or not row.get('earnings_date'):
        return [], ['earnings_unknown']
    earnings_date = row['earnings_date']
    if valid_from <= earnings_date <= valid_until:
        return ['earnings_in_validity'], []
    if earnings_date < signal_date:
        return [], ['earnings_unknown']
    if earnings_date <= add_trading_days(signal_date, EARNINGS_WARN_SESSIONS):
        return [], ['earnings_within_10d']
    return [], []


def size(*, snapshot: dict[str, Any] | None, ref_price: Decimal, stop: Decimal, entry_high: Decimal,
         ticker_value: Decimal, total_value: Decimal, params: WorkbenchConfig) -> tuple[dict[str, Any], list[str]]:
    """Share count as the smallest of the risk budget and the two exposure caps.

    No snapshot means no equity is known: per-share risk only, status no_equity.
    A non-positive result means no room under the caps (reason no_room, shares 0).
    """
    per_share_risk = ref_price - stop
    base = {'risk_pct': params.risk_pct, 'single_cap': params.single_cap, 'total_cap': params.total_cap,
            'per_share_risk': per_share_risk}
    if snapshot is None:
        return ({'equity': None, 'snapshot_id': None, 'snapshot_as_of': None, **base,
                 'risk_amount': None, 'shares': None, 'applied_cap': None, 'status': 'no_equity'}, [])
    equity = _dec(snapshot['equity'])
    risk_amount = equity * _dec(params.risk_pct)
    caps = (
        ('risk', _floor(risk_amount / per_share_risk)),
        ('single', _floor((equity * _dec(params.single_cap) - ticker_value) / entry_high)),
        ('total', _floor((equity * _dec(params.total_cap) - total_value) / entry_high)),
    )
    applied_cap, shares = min(caps, key=lambda cap: cap[1])
    invalid = ['no_room'] if shares <= 0 else []
    status = 'no_room' if shares <= 0 else ('sized' if applied_cap == 'risk' else 'capped')
    return ({'equity': equity, 'snapshot_id': snapshot['id'], 'snapshot_as_of': snapshot['as_of'], **base,
             'risk_amount': risk_amount, 'shares': shares, 'applied_cap': applied_cap, 'status': status},
            invalid)


def validate_levels(raw: dict[str, Any]) -> dict[str, Decimal]:
    """User-submitted levels: numeric, finite, positive and strictly ordered."""
    levels: dict[str, Decimal] = {}
    for key in LEVEL_KEYS:
        try:
            value = _dec(raw[key])
        except (InvalidOperation, TypeError, KeyError):
            raise ValueError(f'{key} must be a number') from None
        if not value.is_finite() or value <= 0:
            raise ValueError(f'{key} must be a positive number')
        levels[key] = value
    if not levels_ordered(levels):
        raise ValueError('levels must satisfy stop < entry_low <= entry_high < target1')
    return levels


def _inputs(bars: list[dict[str, Any]], earnings_row: dict[str, Any] | None,
            snapshot: dict[str, Any] | None, exposure: dict[str, Any]) -> dict[str, Any]:
    return {
        'bar_ids': [b['id'] for b in bars[-MIN_BARS:]],
        'earnings_date_id': earnings_row['id'] if earnings_row else None,
        'snapshot_id': snapshot['id'] if snapshot else None,
        'exposure': exposure,
    }


def context_flags(context: dict | None) -> list[str]:
    """Advisory flags from frozen market context; never affect levels or sizing."""
    if not context:
        return []
    flags: list[str] = []
    regime = context.get('regime')
    if regime is None:
        flags.append('regime_unavailable')
    elif regime.get('label') == 'risk_off':
        flags.append('regime_risk_off')
    sector = context.get('sector')
    if sector and sector.get('state') == 'weak':
        flags.append('sector_weak')
    return flags


def _freeze_inputs(inputs: dict[str, Any], context: dict | None) -> dict[str, Any]:
    """Context is frozen verbatim into inputs; no context keeps the P1 shape exactly."""
    if context is None:
        return inputs
    return {**inputs, 'context': context}


def _exec_fields(version: str, max_hold_sessions: int | None = None) -> dict[str, Any]:
    """The columns plan-2 adds (empty for plan-1, whose rows keep both NULL)."""
    if version == PLAN_V1:
        return {}
    if version != PLAN_V2:
        raise ValueError(f'unknown plan version: {version!r}')
    hold = RULE_MAX_HOLD_SESSIONS if max_hold_sessions is None else max_hold_sessions
    if isinstance(hold, bool) or not isinstance(hold, int) or not MAX_HOLD_RANGE[0] <= hold <= MAX_HOLD_RANGE[1]:
        raise ValueError(f'max_hold_sessions must be an integer in {MAX_HOLD_RANGE}')
    return {'entry_type': DEFAULT_ENTRY_TYPE, 'max_hold_sessions': hold}


def _exec_inputs(version: str, inputs: dict[str, Any], active_chain: dict[str, Any] | None = None) -> dict[str, Any]:
    if version == PLAN_V1:
        return inputs
    extra: dict[str, Any] = {'entry_rules_version': ENTRY_RULES_VERSION}
    if active_chain is not None:
        extra['active_chain'] = active_chain
    return {**inputs, **extra}


def build_rule_plan(*, ticker: str, signal_date: date, bars: list[dict[str, Any]], earnings_row: dict[str, Any] | None,
                    snapshot: dict[str, Any] | None, exposure: dict[str, Any],
                    params: WorkbenchConfig, context: dict | None = None, version: str = PLAN_V1,
                    active_chain: dict[str, Any] | None = None) -> dict[str, Any]:
    """The rule plan for a buy unit; {'skip': 'atr_unavailable'} when the bars cannot price it.

    `version=PLAN_V2` adds `entry_type`, `max_hold_sessions` and `inputs.entry_rules_version` (and
    `inputs.active_chain = {ticker, signal_date}` when the unit joined an active chain); levels, validity,
    sizing and flags are identical in both versions."""
    atr = _freeze(atr14(bars)) if (raw := atr14(bars)) is not None else None
    if atr is None or atr <= 0:
        return {'skip': 'atr_unavailable'}
    ref_price = _freeze(_dec(bars[-1]['close']))
    levels = rule_levels(ref_price, atr)
    if not levels_ordered(levels):
        return {'skip': 'atr_unavailable'}
    valid_from, valid_until = validity(signal_date)
    invalid, warnings = earnings_flags(earnings_row, signal_date=signal_date, valid_from=valid_from,
                                       valid_until=valid_until)
    sizing, size_invalid = size(snapshot=snapshot, ref_price=ref_price, stop=levels['stop'],
                                entry_high=levels['entry_high'], ticker_value=exposure['ticker_value'],
                                total_value=exposure['total_value'], params=params)
    invalid_reasons = [*invalid, *size_invalid]
    return {
        'ticker': ticker, 'signal_date': signal_date, 'kind': 'rule', 'based_on_id': None,
        'plan_version': version, 'ref_price': ref_price, 'atr': atr, **levels,
        'trail_atr_multiple': TRAIL_ATR_MULTIPLE, 'valid_from': valid_from, 'valid_until': valid_until,
        'sizing': sizing, 'status': 'invalid' if invalid_reasons else 'ok',
        'invalid_reasons': invalid_reasons, 'warnings': [*warnings, *context_flags(context)],
        'inputs': _exec_inputs(version, _freeze_inputs(_inputs(bars, earnings_row, snapshot, exposure), context),
                               active_chain),
        **_exec_fields(version),
    }


def build_own_plan(*, rule_plan: dict[str, Any] | None, levels: dict[str, Decimal], ticker: str,
                   signal_date: date, bars: list[dict[str, Any]], earnings_row: dict[str, Any] | None,
                   snapshot: dict[str, Any] | None, exposure: dict[str, Any],
                   params: WorkbenchConfig, context: dict | None = None, version: str = PLAN_V1,
                   fill_session: date | None = None, max_hold_sessions: int | None = None) -> dict[str, Any]:
    """A user-confirmed variant; rule-plan invalid reasons demote to warnings (spec §4.5).

    `fill_session` (v2.9 spec §6.1) makes the validity the fill day alone, for a plan written together with a
    fill; `version=PLAN_V2` adds the execution fields (`max_hold_sessions` defaults to 10)."""
    valid_from, valid_until = validity(signal_date) if fill_session is None else (fill_session, fill_session)
    invalid, warnings = earnings_flags(earnings_row, signal_date=signal_date, valid_from=valid_from,
                                       valid_until=valid_until)
    atr = _freeze(atr14(bars)) if (raw := atr14(bars)) is not None else None
    if atr is None:
        atr = rule_plan['atr'] if rule_plan is not None and rule_plan.get('atr') is not None else Decimal(0)
    ref_price = _freeze(_dec(bars[-1]['close']))
    # Risk per share is priced from the user's own entry band (its midpoint, as the rule
    # plan's ref sits mid-band), not the signal close the own levels may sit above.
    own_entry = (levels['entry_low'] + levels['entry_high']) / 2
    sizing, size_invalid = size(snapshot=snapshot, ref_price=own_entry, stop=levels['stop'],
                                entry_high=levels['entry_high'], ticker_value=exposure['ticker_value'],
                                total_value=exposure['total_value'], params=params)
    return {
        'ticker': ticker, 'signal_date': signal_date, 'kind': 'own',
        'based_on_id': rule_plan['id'] if rule_plan is not None else None,
        'plan_version': version, 'ref_price': ref_price, 'atr': atr, **levels,
        'trail_atr_multiple': TRAIL_ATR_MULTIPLE, 'valid_from': valid_from, 'valid_until': valid_until,
        'sizing': sizing, 'status': 'ok', 'invalid_reasons': [],
        'warnings': [*warnings, *invalid, *size_invalid, *context_flags(context)],
        'inputs': _exec_inputs(version, _freeze_inputs(_inputs(bars, earnings_row, snapshot, exposure), context)),
        **_exec_fields(version, max_hold_sessions),
    }


def build_agent_plan(*, levels: dict[str, Decimal], agent_run_id: str, model: str, ticker: str,
                     signal_date: date, bars: list[dict[str, Any]], earnings_row: dict[str, Any] | None,
                     snapshot: dict[str, Any] | None, exposure: dict[str, Any],
                     params: WorkbenchConfig, context: dict | None = None) -> dict[str, Any]:
    """An ok agent suggestion becomes a plan: its four levels, sizing from the entry
    band midpoint (like an own plan); no ATR freezes 0, so the trailing stop
    degenerates to breakeven (spec §7)."""
    valid_from, valid_until = validity(signal_date)
    invalid, warnings = earnings_flags(earnings_row, signal_date=signal_date, valid_from=valid_from,
                                       valid_until=valid_until)
    atr = _freeze(atr14(bars)) if (raw := atr14(bars)) is not None else None
    if atr is None:
        atr = Decimal(0)
    ref_price = _freeze(_dec(bars[-1]['close']))
    entry_mid = (levels['entry_low'] + levels['entry_high']) / 2
    sizing, size_invalid = size(snapshot=snapshot, ref_price=entry_mid, stop=levels['stop'],
                                entry_high=levels['entry_high'], ticker_value=exposure['ticker_value'],
                                total_value=exposure['total_value'], params=params)
    invalid_reasons = [*invalid, *size_invalid]
    inputs = _freeze_inputs(_inputs(bars, earnings_row, snapshot, exposure), context)
    return {
        'ticker': ticker, 'signal_date': signal_date, 'kind': 'agent', 'based_on_id': None,
        'plan_version': AGENT_VERSION, 'ref_price': ref_price, 'atr': atr, **levels,
        'trail_atr_multiple': TRAIL_ATR_MULTIPLE, 'valid_from': valid_from, 'valid_until': valid_until,
        'sizing': sizing, 'status': 'invalid' if invalid_reasons else 'ok',
        'invalid_reasons': invalid_reasons, 'warnings': [*warnings, *context_flags(context)],
        'inputs': {**inputs, 'agent_run_id': agent_run_id, 'model': model},
    }
