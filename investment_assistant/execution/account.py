"""Account ledger pure functions (v3.1 spec §8): cash by the §8.2 formula, equity, unrealized
and realized pnl, the sizing-equity source of truth and reconciliation differences.

No SQL, no facts imports. Amounts and prices are Decimal; positions come from
execution.positions.build (FIFO, fees amortized into cost).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from investment_assistant.execution.positions import build


def _live(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("voided_at") is None]


def ledger(
    *, cash_flows: list[dict], trades: list[dict], closes: dict[str, Decimal], splits: dict[str, list] | None = None
) -> dict:
    """The account view of spec §8.3. `cash` follows §8.2 and only counts trades executed at
    or after the opening's effective_at; trades before it still form positions. Without an
    opening cash/equity are None. More than one live opening is rejected by the writers;
    here the latest effective_at wins.
    """
    flows = _live(cash_flows)
    openings = [flow for flow in flows if flow["kind"] == "opening"]
    opening = (
        max(openings, key=lambda flow: (flow["effective_at"], flow.get("id") or 0), default=None) if openings else None
    )
    cash: Decimal | None = None
    if opening is not None:
        cash = Decimal(opening["amount"])
        for flow in flows:
            if flow is opening or flow["effective_at"] < opening["effective_at"]:
                continue
            amount = Decimal(flow["amount"])
            if flow["kind"] == "deposit" or flow["kind"] == "adjustment":
                cash += amount
            elif flow["kind"] == "withdrawal":
                cash -= amount
        for trade in _live(trades):
            if trade["executed_at"] < opening["effective_at"]:
                continue
            gross = Decimal(trade["quantity"]) * Decimal(trade["price"])
            fee = Decimal(trade.get("fee") or 0)
            cash += gross - fee if trade["side"] == "sell" else -(gross + fee)

    positions: list[dict] = []
    market_value = Decimal(0)
    realized_pnl = Decimal(0)
    by_ticker: dict[str, list[dict]] = {}
    for trade in trades:
        by_ticker.setdefault(trade["ticker"], []).append(trade)
    for ticker in sorted(by_ticker):
        built = build(by_ticker[ticker], splits=(splits or {}).get(ticker))
        realized_pnl += sum((lot["realized_pnl"] for lot in built["closed"]), Decimal(0))
        position = built["position"]
        if position is None:
            continue
        last_close = closes.get(ticker)
        market = None if last_close is None else position["quantity"] * last_close
        unrealized = None if last_close is None else position["quantity"] * (last_close - position["avg_cost"])
        positions.append(
            {
                "ticker": ticker,
                "quantity": position["quantity"],
                "avg_cost": position["avg_cost"],
                "last_close": last_close,
                "market_value": market,
                "unrealized_pnl": unrealized,
                "unrealized_pct": (None if last_close is None else last_close / position["avg_cost"] - 1),
            }
        )
        if market is not None:
            market_value += market
    cash_negative = cash is not None and cash < 0
    return {
        "opening": opening,
        "cash": cash,
        "positions": positions,
        "market_value": market_value,
        "equity": None if cash is None else cash + market_value,
        "realized_pnl": realized_pnl,
        "cash_negative": cash_negative,
        "notes": ["现金为负，可能漏记入金"] if cash_negative else [],
    }


def sizing_snapshot(*, ledger: dict, latest_equity: dict | None, as_of: date) -> dict | None:
    """The snapshot shape plan_levels.size expects: the ledger's equity when an opening
    exists, else the latest reconciliation row, else None (spec §8.3)."""
    if ledger.get("opening") is not None:
        return {"id": None, "as_of": as_of, "equity": ledger["equity"], "source": "ledger"}
    if latest_equity is not None:
        return {
            "id": latest_equity["id"],
            "as_of": latest_equity["as_of"],
            "equity": latest_equity["equity"],
            "source": "reconciliation",
        }
    return None


def reconcile(*, reported: Decimal, computed: Decimal | None) -> dict:
    """The difference between the broker-reported equity and our own (spec §8.5)."""
    return {"reported": reported, "computed": computed, "difference": None if computed is None else reported - computed}
