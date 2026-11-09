"""Latest successful market facts and the shared watch table."""

from datetime import timedelta

from test_api_chat import NOW, SESSION, seed_run
from test_api_today import seed

from investment_assistant.facts import repo as facts_repo

MARKET = {
    "calc_version": "regime-v3",
    "label": "neutral",
    "regime_tier": "neutral",
    "breadth": {
        "tickers": 100,
        "eligible_50": 90,
        "above_50": 45,
        "eligible_200": 80,
        "above_200": 40,
        "eligible_52w": 70,
        "new_highs": 4,
        "new_lows": 2,
        "pct_50": 0.5,
        "pct_200": 0.5,
    },
    "spy_ret20": 0.01,
    "etf_table": [{"etf": "XLK", "ret20": 0.02, "rs20": 0.01, "rs60": None, "state": "mixed"}],
    "macro": {"DGS10": {"value": 4.1, "obs_date": "2026-11-04", "change": 0.1, "change_from": "2026-10-05"}},
}


def test_market_returns_latest_success_and_watch_rows(client, v3_tx):
    seed(v3_tx)
    facts_repo.insert_run(
        v3_tx,
        {
            "run_id": "market-ok",
            "session": SESSION,
            "trigger": "manual_batch",
            "status": "ok",
            "market": MARKET,
            "draft_md": "",
            "started_at": NOW,
            "finished_at": NOW,
        },
    )
    facts_repo.insert_run(
        v3_tx,
        {
            "run_id": "market-error",
            "session": SESSION + timedelta(days=1),
            "trigger": "manual_batch",
            "status": "error",
            "error": "failed",
            "draft_md": "",
            "started_at": NOW,
            "finished_at": NOW,
        },
    )
    v3_tx.commit()
    response = client.get("/api/market")
    assert response.status_code == 200
    body = response.json()
    from investment_assistant.core.tx import _jsonable
    from investment_assistant.facts.watch import watch_rows

    assert body == {
        **MARKET,
        "session": SESSION.isoformat(),
        "watch": _jsonable(watch_rows(v3_tx, session=SESSION, cutoff_at=NOW)),
    }
    assert body["watch"][0]["source"] == "holding"


def test_market_empty_database_is_no_run(client, v3_tx):
    response = client.get("/api/market")
    assert response.status_code == 404 and response.json() == {"error": "no_run"}


def test_market_missing_calc_version_is_null(client, v3_tx):
    seed_run(v3_tx)
    v3_tx.commit()
    response = client.get("/api/market")
    assert response.status_code == 200 and response.json()["calc_version"] is None
