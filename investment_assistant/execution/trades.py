"""execution trades use cases; model calls belong to analysis."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import REAL_ACCOUNT_ID
from investment_assistant.core.tx import Gone
from investment_assistant.execution import repo as execution_repo

from investment_assistant.execution import ledger, account_records as account

DECISIONS_RETIRED = 'decisions_retired: v2.9 起不再记录决定，采纳由成交推导；可在计划上写备注'


def decide(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """v2.9 D24: there is no decision button any more; adoption comes from the fills. Always ``Gone`` (410), nothing is
    written. ``signal_decisions`` keeps its old rows, read-only."""
    raise Gone(DECISIONS_RETIRED)


def add_trade(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return ledger.record_trade(conn, payload, now=now)


def add_plan_note(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """v2.9 §6.5: a note on a chain or a non-chain plan, a correction or a retraction. Never read by an Agent."""
    with core_tx._tx() as conn:
        return ledger.record_note(conn, payload, now=now)


def list_trades(*, account_id=REAL_ACCOUNT_ID) -> list[dict[str, Any]]:
    """Every fill, newest first. A fill that follows a plan carries ``plan_label`` (the label the link list uses)."""

    with core_tx.read_only_tx() as conn:
        rows = list(reversed(execution_repo.list_trades(conn, account_id=ledger._uuid(account_id, 'account_id'))))
        labels_ = trade_plan_labels(conn, rows)
        return [{**t, 'plan_label': labels_[t['id']]} if t['id'] in labels_ else t for t in rows]


def void_trade(trade_id: str, reason: Any, *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return ledger.void_trade(conn, trade_id, reason, now=now)


def add_snapshot(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return account.record_snapshot(conn, payload, now=now)


def add_flow(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return account.record_flow(conn, payload, now=now)


def void_flow(flow_id: str, reason: Any, *, now: datetime) -> dict[str, Any]:
    with core_tx._tx() as conn:
        return account.void_flow(conn, flow_id, reason, now=now)


def _money(value: Any) -> str | None:
    return None if value is None else f'{Decimal(str(value)):.2f}'


def _mmdd(day: date) -> str:
    return day.strftime('%m-%d')


def chain_label(ticker: str, signal_date: date, version_no: int) -> str:
    return f'{ticker}·{_mmdd(signal_date)} 第 {version_no} 版'


def _link_label(prefix: str, plan: dict[str, Any], *, state: str | None = None) -> str:
    until = _mmdd(plan['valid_until'])
    return f'{prefix}（{state + "，" if state else ""}有效至 {until}）'


def plan_label(plan: dict[str, Any], *, version_no: int | None = None, state: str | None = None) -> str:
    """The label of a plan as the link list and the trade list show it: 「Agent MU·10-29 第 2 版（入场期，有效至 11-03）」,
    「规则基线 MU·10-29（有效至 11-04）」, 「自定 MU·10-28 入场 95.00–97.00（有效 10-28）」."""
    ticker = plan['ticker']
    if plan['kind'] == 'agent' and version_no is not None:
        return _link_label(f"Agent {chain_label(ticker, plan['signal_date'], version_no)}", plan, state=state)
    if plan['kind'] == 'own':
        return (f"自定 {ticker}·{_mmdd(plan['signal_date'])} 入场 {_money(plan['entry_low'])}–{_money(plan['entry_high'])}"
                f"（有效 {_mmdd(plan['valid_from'])}）")
    prefix = '规则基线' if plan['kind'] == 'rule' else 'Agent'
    return _link_label(f"{prefix} {ticker}·{_mmdd(plan['signal_date'])}", plan)


def trade_plan_labels(conn, trades: list[dict[str, Any]]) -> dict[Any, str]:
    """``plan_label`` of the plan each trade follows (trades without ``plan_id`` are absent)."""
    out: dict[Any, str] = {}
    for trade in trades:
        if trade.get('plan_id') is None:
            continue
        plan = execution_repo.plan_by_id(conn, trade['plan_id'])
        if plan is None:
            continue
        version = execution_repo.version_by_plan(conn, plan['id']) if plan['kind'] == 'agent' else None
        out[trade['id']] = plan_label(plan, version_no=version['version_no'] if version else None)
    return out
