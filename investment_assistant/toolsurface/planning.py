"""Plan tools: compute_plan_levels plus the host-validated save_verdict / save_plan (spec §6 算/写)."""
from __future__ import annotations

from decimal import Decimal

from investment_assistant.data import repo as data_repo
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.execution.entry_rules import ENTRY_TYPES
from investment_assistant.execution.plan_levels import (
    MAX_HOLD_RANGE,
    earnings_flags,
    rule_levels,
    size,
    validate_levels,
    validity,
)
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.reads import holdings_rows, session_bars, top_tickers
from investment_assistant.toolsurface.validation import check_agent_levels, in_scope, normalize_ticker

VERDICTS = ('confirm', 'veto', 'unknown')
NO_EQUITY_TEXT = '未提供账户资金，未算股数'


def _equity_snapshot(conn) -> dict | None:
    """最近一次记录的账户本金，只取仓位计算用的三个字段（spec §6 我的判断）。"""
    row = facts_repo.latest_equity(conn)
    return None if row is None else {'id': row['id'], 'as_of': row['as_of'], 'equity': row['equity']}


def _exposure(conn, env: ToolEnv, ticker: str) -> tuple[Decimal, Decimal]:
    """(该标的市值, 持仓总市值)：持仓数量乘该交易日口径的最新收盘。"""
    ticker_value, total_value = Decimal(0), Decimal(0)
    for row in holdings_rows(conn, env):
        if 'error' in row or row.get('last_close') is None:
            continue
        value = row['quantity'] * Decimal(str(row['last_close']))
        total_value += value
        if row['ticker'] == ticker:
            ticker_value = value
    return ticker_value, total_value


def _bars_atr(conn, env: ToolEnv, ticker: str) -> tuple[list[dict], Decimal]:
    bars = session_bars(conn, env, ticker)
    if not bars:
        raise ValueError('no_bars')
    atr = price_indicators.atr14(bars)
    if atr is None or atr <= 0:
        raise ValueError('atr_unavailable')
    return bars, atr


def compute_plan_levels(env: ToolEnv, *, ticker: str) -> dict:
    """宿主算术：规则价位、有效期、按风险预算的股数；金额与价位一律转字符串。"""
    ticker = normalize_ticker(ticker)

    def _read(conn):
        bars, atr = _bars_atr(conn, env, ticker)
        ref_price = Decimal(str(bars[-1]['close']))            # session 当天未复权 close
        levels = rule_levels(ref_price, atr)
        valid_from, valid_until = validity(env.session)
        snapshot = _equity_snapshot(conn)
        ticker_value, total_value = _exposure(conn, env, ticker)
        sizing, _ = size(snapshot=snapshot, ref_price=ref_price, stop=levels['stop'],
                         entry_high=levels['entry_high'], ticker_value=ticker_value,
                         total_value=total_value, params=env.cfg.workbench)
        earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=env.cutoff_at)
        invalid, warnings = earnings_flags(earnings, signal_date=env.session,
                                           valid_from=valid_from, valid_until=valid_until)
        return {'ticker': ticker, 'session': env.session, 'ref_price': str(ref_price), 'atr': str(atr),
                'levels': {key: str(value) for key, value in levels.items()},
                'valid_from': valid_from, 'valid_until': valid_until, 'sizing': sizing,
                'earnings': {'invalid': invalid, 'warnings': warnings},
                'entry_types': list(ENTRY_TYPES), 'max_hold_range': list(MAX_HOLD_RANGE)}
    return env.read(_read)


def save_verdict(env: ToolEnv, *, ticker: str, verdict: str, reasons: str, counter: str = '',
                 refs: list | None = None) -> dict:
    ticker = normalize_ticker(ticker)
    refs = list(refs or [])
    if verdict not in VERDICTS:
        raise ValueError('invalid_verdict')
    if not isinstance(reasons, str) or not 1 <= len(reasons) <= 2000:
        raise ValueError('invalid_argument: reasons 需为 1–2000 字')
    text = counter if isinstance(counter, str) else ''
    if not isinstance(counter, str) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError('invalid_argument: counter / refs 类型不符')

    def _work(conn):
        if not in_scope(env.mode, ticker, top_tickers(conn, env)):
            raise ValueError('out_of_scope')
        known = facts_repo.tool_call_refs(conn, env.agent_session)
        if any(ref not in known for ref in refs) or (verdict in ('confirm', 'veto') and not refs):
            raise ValueError('invalid_refs')
        return facts_repo.upsert_verdict(conn, {'session': env.session, 'ticker': ticker,
                                                'verdict': verdict, 'reasons': reasons, 'counter': text,
                                                'refs': refs, 'origin': env.mode,
                                                'agent_session': env.agent_session})
    return env.write(_work)


def _levels_of(env: ToolEnv, conn, ticker: str, levels: dict | None,
               last_close: Decimal, atr: Decimal) -> tuple[dict[str, Decimal], str, Decimal]:
    """(价位, 来源, 仓位基准价)：agent 价位过两道校验；缺省取宿主规则价位。"""
    if levels is None:
        return rule_levels(last_close, atr), 'host', last_close
    try:
        chosen = validate_levels(levels)
    except ValueError as exc:
        raise ValueError(f'levels_invalid: {exc}') from None
    problems = check_agent_levels(chosen, last_close=last_close, atr=atr)
    if problems:
        raise ValueError(f"levels_invalid: {', '.join(problems)}")
    # 与自有计划同口径：风险按入场带中点计（spec §6 价位校验、§7 现有算术原样）
    return chosen, 'agent', (chosen['entry_low'] + chosen['entry_high']) / 2


def save_plan(env: ToolEnv, *, ticker: str, thesis: str, invalidation: str,
              entry_type: str = 'zone_limit', max_hold_sessions: int = 10, levels: dict | None = None,
              counter: str = '', refs: list | None = None) -> dict:
    """检查顺序固定：范围 → 确认结论 → 入场类型 → 最长持有 → 价位 → 财报日（spec §6）。"""
    ticker = normalize_ticker(ticker)
    refs = list(refs or [])
    if not isinstance(thesis, str) or not isinstance(invalidation, str) or not isinstance(counter, str) \
            or any(not isinstance(ref, str) for ref in refs):
        raise ValueError('invalid_argument: thesis / invalidation / counter / refs 类型不符')
    hold = max_hold_sessions if isinstance(max_hold_sessions, int) \
        and not isinstance(max_hold_sessions, bool) else None

    def _work(conn):
        if not in_scope(env.mode, ticker, top_tickers(conn, env)):
            raise ValueError('out_of_scope')
        verdict = facts_repo.verdicts(conn, env.session).get(ticker)
        if verdict is None or verdict['verdict'] != 'confirm':
            raise ValueError('no_confirm_verdict')
        if entry_type not in ENTRY_TYPES:
            allowed = '、'.join(ENTRY_TYPES)
            raise ValueError(f'invalid_entry_type: 需为 {allowed} 之一')
        if hold is None or not MAX_HOLD_RANGE[0] <= hold <= MAX_HOLD_RANGE[1]:
            raise ValueError(f"invalid_max_hold: 需为 {MAX_HOLD_RANGE[0]}–"
                             f"{MAX_HOLD_RANGE[1]} 的整数")
        if levels is not None and not isinstance(levels, dict):
            raise ValueError('invalid_argument: levels 必须是对象')
        bars, atr = _bars_atr(conn, env, ticker)
        last_close = Decimal(str(bars[-1]['close']))
        chosen, source, sizing_ref = _levels_of(env, conn, ticker, levels, last_close, atr)
        valid_from, valid_until = validity(env.session)
        earnings = data_repo.latest_earnings(conn, ticker, cutoff_at=env.cutoff_at)
        invalid, warnings = earnings_flags(earnings, signal_date=env.session,
                                           valid_from=valid_from, valid_until=valid_until)
        if 'earnings_in_validity' in invalid:
            raise ValueError('earnings_in_validity')
        snapshot = _equity_snapshot(conn)
        ticker_value, total_value = _exposure(conn, env, ticker)
        sizing, size_invalid = size(snapshot=snapshot, ref_price=sizing_ref,
                                    stop=chosen['stop'], entry_high=chosen['entry_high'],
                                    ticker_value=ticker_value, total_value=total_value,
                                    params=env.cfg.workbench)
        plan_warnings = [*warnings, *size_invalid]
        if sizing['status'] == 'no_equity':
            plan_warnings.append(NO_EQUITY_TEXT)
        return facts_repo.upsert_plan(conn, {
            'session': env.session, 'ticker': ticker, 'origin': env.mode, 'entry_type': entry_type,
            'entry_low': chosen['entry_low'], 'entry_high': chosen['entry_high'], 'stop': chosen['stop'],
            'target1': chosen['target1'], 'levels_source': source, 'max_hold_sessions': hold,
            'valid_from': valid_from, 'valid_until': valid_until, 'shares': sizing['shares'],
            'sizing': sizing, 'warnings': plan_warnings, 'thesis': thesis, 'invalidation': invalidation,
            'counter': counter, 'refs': refs, 'agent_session': env.agent_session})
    return env.write(_work)
