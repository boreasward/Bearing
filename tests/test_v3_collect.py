"""v3 collection driver: family order, worker mapping, whole-family failures and the extra backfill."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.core import tx as core_tx
from investment_assistant.data import registry as data_registry
from investment_assistant.data.sources.edgar import COMPANY_TICKERS_TARGET
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def test_target_facts(v3_tx):
    cfg = AssistantConfig()
    for ticker in ("AAPL", "MSFT"):
        v3_tx.execute(
            "INSERT INTO watchlist_items (ticker, status, thesis, tags) VALUES (%s, 'active', '', %s)", (ticker, [])
        )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "t1",
            "ticker": "NVDA",
            "side": "buy",
            "quantity": 10,
            "price": Decimal(100),
            "executed_at": NOW,
        },
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "t2",
            "ticker": "AMZN",
            "side": "buy",
            "quantity": 10,
            "price": Decimal(100),
            "executed_at": NOW,
        },
    )
    facts_repo.insert_trade(
        v3_tx,
        {
            "request_id": "t3",
            "ticker": "AMZN",
            "side": "sell",
            "quantity": 10,
            "price": Decimal(110),
            "executed_at": NOW,
        },
    )  # 平仓：不算持仓
    # 一份待入场计划：即使掉出股票池也要有当天的日线（spec §5 第 1 步）
    plan_repo.insert_plan(
        v3_tx,
        {
            "session": SESSION,
            "ticker": "AMD",
            "origin": "brief",
            "entry_type": "zone_limit",
            "entry_low": Decimal(99),
            "entry_high": Decimal(101),
            "stop": Decimal(96),
            "target1": Decimal(108),
            "initial_stop": Decimal(96),
            "atr": Decimal(2),
            "levels_source": "host",
            "max_hold_sessions": 20,
            "valid_from": SESSION,
            "valid_until": SESSION,
            "shares": None,
            "sizing": {},
            "warnings": [],
            "thesis": "",
            "invalidation": "",
            "counter": "",
            "refs": [],
            "agent_session": "collect-test",
            "status": "pending",
            "kind": "planned",
        },
    )
    facts = facts_collect.target_facts(v3_tx, cfg)
    assert facts.pool == ("AAPL", "MSFT") and facts.holdings == ("AMD", "NVDA")
    assert facts.paper_holdings == () and facts.active_chains == ()
    assert facts.sector_etfs == cfg.v3.sector_etfs
    v3_tx.execute("DELETE FROM watchlist_items")
    assert facts_collect.target_facts(v3_tx, cfg).pool == ()  # 空关注列表不抛异常


def fake_registry(monkeypatch, seen, *, fail_family=None, error=None):
    failure = error if error is not None else RuntimeError("adapter down")

    def fake_run_adapter(
        adapter,
        targets,
        *,
        scope,
        trigger,
        run_id,
        session,
        now,
        deps,
        features,
        tally,
        observe=None,
        whole_call_target=None,
        workers=1,
    ):
        assert scope in ("daily", "screen_new_members") and features == {"backup_sources": True}
        seen.append((adapter.id, list(targets), workers, whole_call_target))
        if fail_family is not None and adapter.id == fail_family:
            raise failure
        tally.set_targets(targets)
        for target in targets:
            tally.ok(target)
        return tally

    monkeypatch.setattr(data_registry, "run_adapter", fake_run_adapter)


def test_collect_daily_runs_families_in_order_and_writes_rows(v3_tx, monkeypatch):
    cfg = AssistantConfig()
    seen = []
    fake_registry(monkeypatch, seen)
    rows = facts_collect.collect_daily(
        session=SESSION, now=NOW, run_id="v3-202611050330-00000000", trigger="scheduled", cfg=cfg
    )
    assert [family for family, _, _, _ in seen] == list(facts_collect.DAILY_FAMILIES)
    assert "sec_text" not in {family for family, _, _, _ in seen}
    bars_targets = next(targets for family, targets, _, _ in seen if family == "bars")
    assert bars_targets[0] == "SPY" and bars_targets[1:14] == list(cfg.v3.sector_etfs)
    expected = {family: 1 for family in facts_collect.DAILY_FAMILIES}
    expected.update({family: cfg.v3.collect_workers for family in facts_collect.YF_FAMILIES})
    expected.update({family: cfg.v3.sec_workers for family in facts_collect.SEC_FAMILIES})
    assert {family: workers for family, _, workers, _ in seen} == expected
    assert next(whole for family, _, _, whole in seen if family == "sec_filings") == COMPANY_TICKERS_TARGET
    assert all(whole is None for family, _, _, whole in seen if family != "sec_filings")
    assert len(rows) == 8
    assert all(
        row["run_id"].startswith("v3-")
        and row["job"] == "v3"
        and row["trigger"] == "scheduled"
        and row["scope"] == "daily"
        for row in rows
    )
    assert v3_tx.execute("SELECT COUNT(*) FROM data_fetch_runs").fetchone()[0] == 8


def test_collect_daily_family_exception_becomes_whole_failure(v3_tx, monkeypatch):
    cfg = AssistantConfig()
    seen = []
    fake_registry(monkeypatch, seen, fail_family="news")
    rows = facts_collect.collect_daily(session=SESSION, now=NOW, run_id="v3-x", trigger="manual_batch", cfg=cfg)
    by_family = {row["family"]: row for row in rows}
    assert [failure["target"] for failure in by_family["news"]["failures"]] == ["*"]
    assert by_family["news"]["status"] == "failed"
    assert by_family["bars"]["status"] == "success" and by_family["universe"]["status"] == "success"
    fake_registry(monkeypatch, seen, fail_family="news", error=core_tx.Unavailable("数据库不可达"))
    with pytest.raises(core_tx.Unavailable):
        facts_collect.collect_daily(session=SESSION, now=NOW, run_id="v3-y", trigger="manual_batch", cfg=cfg)


def test_collect_extra_uses_explicit_targets(v3_tx, monkeypatch):
    cfg = AssistantConfig()
    seen = []
    fake_registry(monkeypatch, seen)
    rows = facts_collect.collect_extra(
        ["AAPL", "MSFT"], session=SESSION, now=NOW, run_id="v3-x", trigger="manual_batch", cfg=cfg
    )
    assert [(family, targets) for family, targets, _, _ in seen] == [
        ("news", ["AAPL", "MSFT"]),
        ("sec_filings", ["AAPL", "MSFT"]),
        ("fundamentals", ["AAPL", "MSFT"]),
    ]
    assert {family: workers for family, _, workers, _ in seen} == {
        "news": cfg.v3.collect_workers,
        "sec_filings": cfg.v3.sec_workers,
        "fundamentals": cfg.v3.sec_workers,
    }
    assert len(rows) == 3 and all(row["scope"] == "screen_new_members" for row in rows)
    seen.clear()
    assert (
        facts_collect.collect_extra([], session=SESSION, now=NOW, run_id="v3-x", trigger="manual_batch", cfg=cfg) == []
    )
    assert seen == []
