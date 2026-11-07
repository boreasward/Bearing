"""The account view assembled from the database (spec §8.3, §8.5): the ledger pure function
over cash flows, trades, latest closes and splits, plus the latest reconciliation, the most
recent close-out outcomes and the sizing-equity snapshot. Reads only."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from investment_assistant.data import repo as data_repo
from investment_assistant.execution.account import ledger, reconcile, sizing_snapshot
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

RECENT_OUTCOMES = 5


def account_view(conn, *, session: date, cutoff_at: datetime) -> dict:
    trades = facts_repo.list_trades(conn)
    closes: dict[str, Decimal] = {}
    splits: dict[str, list] = {}
    for ticker in sorted({trade["ticker"] for trade in trades}):
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
        if bars:
            closes[ticker] = Decimal(str(bars[-1]["close"]))
        splits[ticker] = data_repo.split_events(conn, ticker, cutoff_at=cutoff_at)
    computed = ledger(cash_flows=plan_repo.cash_flows(conn), trades=trades, closes=closes, splits=splits)
    latest = facts_repo.latest_equity(conn)
    reconciliation = None
    if latest is not None:
        reconciliation = {
            "as_of": latest["as_of"],
            **reconcile(reported=Decimal(latest["equity"]), computed=computed["equity"]),
        }
    recent = [
        {
            "plan_id": plan["id"],
            "ticker": plan["ticker"],
            "ended_session": plan["ended_session"],
            "outcome": plan["outcome"] or {},
        }
        for plan in plan_repo.plans_by_status(conn, ("closed",), limit=RECENT_OUTCOMES)
    ]
    return {
        "ledger": computed,
        "latest_equity": latest,
        "reconciliation": reconciliation,
        "recent_outcomes": recent,
        "sizing_snapshot": sizing_snapshot(ledger=computed, latest_equity=latest, as_of=session),
    }
