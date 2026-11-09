"""Ordered, deduplicated market watch rows over the disposable DB."""

from datetime import timedelta
from decimal import Decimal as D

from test_api_today import NOW, SESSION, _bars, seed

from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo, research_repo


def test_watch_rows_prioritizes_plans_and_requires_two_bars(v3_tx):
    seed(v3_tx)
    holding = plan_repo.active_plan(v3_tx, "AAPL")
    assert holding is not None
    template = dict(holding)
    pending = {}
    for ticker in ("MSFT", "NVDA"):
        pending[ticker] = plan_repo.insert_plan(v3_tx, {**template, "ticker": ticker, "status": "pending"})
    research = {}
    for ticker in ("NVDA", "MU"):
        research[ticker] = research_repo.insert_research(
            v3_tx,
            {
                "ticker": ticker,
                "thesis": "观察价格",
                "origin": "chat",
                "created_session": SESSION,
                "valid_until": SESSION + timedelta(days=7),
            },
        )
    for ticker in ("MSFT", "NVDA"):
        bars = _bars(2)
        bars[-1].update(close=96.2, adj_close=96.2)
        data_repo.store_bars(v3_tx, ticker, bars, now=NOW)
    data_repo.store_bars(v3_tx, "MU", _bars(1), now=NOW)
    from investment_assistant.facts.watch import watch_rows

    rows = watch_rows(v3_tx, session=SESSION, cutoff_at=NOW)
    assert [r["ticker"] for r in rows] == ["AAPL", "NVDA", "MSFT", "MU"]
    assert rows[0] == {
        "ticker": "AAPL",
        "source": "holding",
        "plan_id": holding["id"],
        "research_id": None,
        "last_close": "100.0",
        "prev_close": "100.0",
        "change_pct": "0.00",
    }
    assert rows[1]["source"] == "pending" and rows[1]["plan_id"] == pending["NVDA"]["id"]
    assert rows[1]["research_id"] is None
    assert D(rows[1]["last_close"]) == D("96.2") and rows[1]["change_pct"] == "-3.80"
    assert rows[-1] == {
        "ticker": "MU",
        "source": "research",
        "plan_id": None,
        "research_id": research["MU"]["id"],
        "last_close": None,
        "prev_close": None,
        "change_pct": None,
    }
    # Facts after the cutoff must not leak into the watch table.
    assert all(
        r["last_close"] is None for r in watch_rows(v3_tx, session=SESSION, cutoff_at=NOW - timedelta(seconds=1))
    )
