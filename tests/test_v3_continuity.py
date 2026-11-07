"""Watchlist continuity (spec §13 / 验收 A15): streak, new entries and dropped reasons."""

from investment_assistant.facts.continuity import continuity


def _screen(ticker, liquidity="ok", earnings="ok", setup="ok", rank="ok"):
    return {"ticker": ticker, "filters": {"liquidity": liquidity, "earnings": earnings, "setup": setup, "rank": rank}}


def test_two_days_new_streak_and_dropped_reasons():
    today = [{"ticker": "AAPL", "rank": 1}, {"ticker": "MSFT", "rank": 2}, {"ticker": "NVDA", "rank": 3}]
    previous = [
        {"ticker": "AAPL", "rank": 2, "streak": 1},
        {"ticker": "TSLA", "rank": 1, "streak": 3},
        {"ticker": "ORCL", "rank": 4, "streak": 1},
        {"ticker": "IBM", "rank": 5, "streak": 2},
        {"ticker": "AMD", "rank": 6, "streak": 1},
        {"ticker": "KO", "rank": 7, "streak": 1},
    ]
    results = [
        _screen("TSLA", rank="rank_cut"),
        _screen("ORCL", liquidity="illiquid"),
        _screen("IBM", earnings="earnings_soon"),
        _screen("AMD", setup="no_setup"),
    ]
    out = continuity(today=today, previous=previous, screen_results=results, skips={"KO": "缺日线，未评估"})
    assert out["first_run"] is False
    assert out["streak"] == {"AAPL": 2, "MSFT": 1, "NVDA": 1}  # 连续第 2 天 / 新上榜
    assert out["new"] == ["MSFT", "NVDA"]
    dropped = {row["ticker"]: row["reasons"] for row in out["dropped"]}
    assert dropped == {
        "TSLA": ["rank_cut"],
        "ORCL": ["illiquid"],
        "IBM": ["earnings_soon"],
        "AMD": ["no_setup"],
        "KO": ["缺日线，未评估"],
    }


def test_streak_carries_previous_count():
    today = [{"ticker": "AAPL", "rank": 1}]
    previous = [{"ticker": "AAPL", "rank": 3, "streak": 4}]
    out = continuity(today=today, previous=previous, screen_results=[], skips={})
    assert out["streak"] == {"AAPL": 5} and out["new"] == [] and out["dropped"] == []


def test_first_run_without_previous():
    today = [{"ticker": "AAPL", "rank": 1}, {"ticker": "MSFT", "rank": 2}]
    out = continuity(today=today, previous=None, screen_results=[], skips={})
    assert out["first_run"] is True
    assert out["streak"] == {"AAPL": 1, "MSFT": 1}
    assert out["new"] == ["AAPL", "MSFT"]
    assert out["dropped"] == []
