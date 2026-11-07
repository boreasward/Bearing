"""Account ledger pure functions (spec §8): cash, equity, unrealized/realized pnl, sizing snapshot."""

from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from investment_assistant.execution import account

NY = ZoneInfo("America/New_York")


def flow(fid, kind, amount, *, day, voided=None):
    return {
        "id": fid,
        "kind": kind,
        "amount": D(str(amount)),
        "note": "",
        "effective_at": datetime(2026, 1, day, 10, tzinfo=NY),
        "voided_at": voided,
    }


def trade(tid, ticker, side, qty, price, *, day, fee=0, voided=None):
    return {
        "id": tid,
        "ticker": ticker,
        "side": side,
        "quantity": D(str(qty)),
        "price": D(str(price)),
        "fee": D(str(fee)),
        "executed_at": datetime(2026, 1, day, 15, tzinfo=NY),
        "voided_at": voided,
    }


def test_ledger_matches_hand_calculation():
    flows = [flow(1, "opening", 10000, day=1), flow(2, "deposit", 2000, day=5)]
    trades = [trade(3, "AAPL", "buy", 100, 50, day=10, fee=1), trade(4, "AAPL", "sell", 40, 60, day=15, fee=1)]
    book = account.ledger(cash_flows=flows, trades=trades, closes={"AAPL": D(55)})
    assert book["opening"]["amount"] == D(10000)
    assert book["cash"] == D(9398)  # 10000 + 2000 − 5001 + 2399
    assert book["market_value"] == D(3300)  # 60 × 55
    assert book["equity"] == D(12698)
    assert book["realized_pnl"] == D("398.6")  # 40×60 − 1 − 40×50.01
    position = book["positions"][0]
    assert position["ticker"] == "AAPL" and position["quantity"] == D(60)
    assert position["avg_cost"] == D("50.01")
    assert position["market_value"] == D(3300)
    assert position["unrealized_pnl"] == D("299.4")  # 60 × (55 − 50.01)
    assert position["unrealized_pct"] == D(55) / D("50.01") - 1
    assert book["cash_negative"] is False and book["notes"] == []


def test_trades_before_opening_do_not_touch_cash():
    flows = [flow(1, "opening", 10000, day=10)]
    trades = [trade(2, "AAPL", "buy", 100, 50, day=5)]  # 早于期初生效时间
    book = account.ledger(cash_flows=flows, trades=trades, closes={"AAPL": D(55)})
    assert book["cash"] == D(10000)
    assert book["positions"][0]["ticker"] == "AAPL"  # 持仓仍在
    assert book["realized_pnl"] == D(0)


def test_voided_rows_ignored():
    voided_at = datetime(2026, 1, 6, 12, tzinfo=NY)
    flows = [
        flow(1, "opening", 10000, day=1),
        flow(2, "deposit", 2000, day=5, voided=voided_at),
        flow(3, "opening", 20000, day=8, voided=voided_at),
    ]  # 作废的期初不生效
    trades = [trade(4, "AAPL", "buy", 100, 50, day=10, voided=voided_at)]
    book = account.ledger(cash_flows=flows, trades=trades, closes={"AAPL": D(55)})
    assert book["opening"]["id"] == 1
    assert book["cash"] == D(10000)  # 作废入金与作废买入都不计
    assert book["positions"] == []
    assert book["equity"] == D(10000)


def test_negative_cash_is_noted():
    flows = [flow(1, "opening", 100, day=1)]
    trades = [trade(2, "AAPL", "buy", 200, 1, day=5)]
    book = account.ledger(cash_flows=flows, trades=trades, closes={"AAPL": D(1)})
    assert book["cash"] == D(-100)
    assert book["cash_negative"] is True
    assert "现金为负，可能漏记入金" in book["notes"]
    assert book["equity"] == D(100)  # −100 + 200


def test_no_opening_leaves_cash_none():
    trades = [trade(1, "AAPL", "buy", 100, 50, day=5)]
    book = account.ledger(cash_flows=[flow(2, "deposit", 2000, day=1)], trades=trades, closes={"AAPL": D(55)})
    assert book["opening"] is None and book["cash"] is None and book["equity"] is None
    assert book["market_value"] == D(5500) and book["cash_negative"] is False


def test_sizing_snapshot_precedence():
    as_of = date(2026, 11, 4)
    with_opening = {"opening": {"id": 1}, "equity": D(12698)}
    latest = {"id": 5, "as_of": date(2026, 11, 3), "equity": D(12300)}
    assert account.sizing_snapshot(ledger=with_opening, latest_equity=latest, as_of=as_of) == {
        "id": None,
        "as_of": as_of,
        "equity": D(12698),
        "source": "ledger",
    }
    without = {"opening": None, "equity": None}
    assert account.sizing_snapshot(ledger=without, latest_equity=latest, as_of=as_of) == {
        "id": 5,
        "as_of": date(2026, 11, 3),
        "equity": D(12300),
        "source": "reconciliation",
    }
    assert account.sizing_snapshot(ledger=without, latest_equity=None, as_of=as_of) is None


def test_reconcile_difference():
    assert account.reconcile(reported=D(1000), computed=D(990)) == {
        "reported": D(1000),
        "computed": D(990),
        "difference": D(10),
    }
    assert account.reconcile(reported=D(1000), computed=None)["difference"] is None
