"""v3 daily collection: which families run in what order, with how many workers, into data_fetch_runs."""

from __future__ import annotations

import secrets
from datetime import date, datetime
from types import SimpleNamespace

import investment_assistant.analysis.pool as pool_module
import investment_assistant.core.tx as core_tx
from investment_assistant.config import AssistantConfig
from investment_assistant.data import collect as data_collect
from investment_assistant.data import registry as data_registry
from investment_assistant.data.adapter_support import adapter_deps_from_service
from investment_assistant.data.adapter_types import AdapterDeps, TargetFacts
from investment_assistant.data.adapters.universe import load as load_universe
from investment_assistant.data.sources.edgar import COMPANY_TICKERS_TARGET
from investment_assistant.db import list_watchlist_items
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo

DAILY_FAMILIES = ("universe", "bars", "earnings", "sector", "fred", "news", "sec_filings", "fundamentals")
EXTRA_FAMILIES = ("news", "sec_filings", "fundamentals")
YF_FAMILIES = ("bars", "news", "earnings")  # cfg.v3.collect_workers
SEC_FAMILIES = ("sec_filings", "fundamentals", "sector")  # cfg.v3.sec_workers
FEATURES = {"backup_sources": True}


def new_run_id(now: datetime) -> str:
    """One run's id: 'v3-YYYYMMDDHHMMSS-<8 hex>'; the tail keeps two runs of the same moment distinct."""
    return f"v3-{now:%Y%m%d%H%M%S}-{secrets.token_hex(4)}"


def target_facts(conn, cfg: AssistantConfig) -> TargetFacts:
    """The day's target facts: watchlist pool plus open v3 positions plus every ticker with an
    active plan (spec §5 第 1 步：掉出股票池的计划也要有当天的日线)；the paper/request queues
    are not v3's."""
    try:
        pool = pool_module.from_watchlist(list_watchlist_items(conn)).tickers
    except ValueError:  # 空或非法关注列表：没有池，不是一个错误
        pool = ()
    by_ticker: dict[str, list[dict]] = {}
    for trade in facts_repo.list_trades(conn):
        by_ticker.setdefault(trade["ticker"], []).append(trade)
    holdings: set[str] = set()
    for ticker in sorted(by_ticker):
        try:
            position = build(by_ticker[ticker])["position"]
        except PositionError:
            continue  # 无法核算的持仓不是采集目标
        if position is not None:
            holdings.add(ticker)
    holdings.update(plan["ticker"] for plan in plan_repo.plans_by_status(conn, ("pending", "open")))
    return TargetFacts(
        pool=tuple(pool),
        holdings=tuple(sorted(holdings)),
        paper_holdings=(),
        active_chains=(),
        sector_etfs=tuple(cfg.v3.sector_etfs),
    )


def _workers(family_id: str, cfg: AssistantConfig) -> int:
    if family_id in YF_FAMILIES:
        return cfg.v3.collect_workers
    if family_id in SEC_FAMILIES:
        return cfg.v3.sec_workers
    return 1


def _locked(families) -> list[str]:
    locked = []
    for family_id in families:
        adapter = data_registry.get(family_id)
        if adapter.kind == "fetched" and adapter.lock:
            locked.append(adapter.id)
    return locked


def _default_deps(cfg: AssistantConfig) -> AdapterDeps:
    return adapter_deps_from_service(
        core_tx.Deps(), transaction=core_tx._tx, config=cfg, sleep=data_collect.FETCH_SLEEP
    )


def _run_family(
    family_id: str, targets, *, session, now, run_id, trigger, cfg, deps, tally, scope: str, observe=None
) -> None:
    """One family's run_adapter call; anything but Unavailable is the family's own whole-call failure."""
    try:
        data_registry.run_adapter(
            data_registry.get(family_id),
            targets,
            scope=scope,
            trigger=trigger,
            run_id=run_id,
            session=session,
            now=now,
            deps=deps,
            features=FEATURES,
            tally=tally,
            observe=observe,
            whole_call_target=COMPANY_TICKERS_TARGET if family_id == "sec_filings" else None,
            workers=_workers(family_id, cfg),
        )
    except core_tx.Unavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 整族失败只记在这一族的行里
        tally.record("*", exc)


def collect_daily(
    *, session: date, now: datetime, run_id: str, trigger: str, cfg: AssistantConfig, deps: AdapterDeps | None = None
) -> list[dict]:
    """Every daily family once, in DAILY_FAMILIES order, one data_fetch_runs row each.

    The universe family runs first and its members become the wide families' targets; when the
    universe could not be had the range file itself is the fallback (an unreadable file leaves no range).
    """
    deps = deps if deps is not None else _default_deps(cfg)
    tallies: dict[str, data_collect.Tally] = {}
    started_at = data_collect.wall_clock()
    with data_collect.family_locks(_locked(DAILY_FAMILIES)):
        with core_tx._tx() as conn:
            facts = target_facts(conn, cfg)
        loaded: list[str] = []

        def observed_universe(target, source_id, rows, result):
            if rows:
                loaded[:] = list(rows[0]["tickers"])

        for family_id in DAILY_FAMILIES:
            tally = tallies[family_id] = data_collect.Tally(family_id, "v3")
            try:
                with core_tx._tx() as conn:
                    if family_id in ("universe", "fred"):
                        targets = data_registry.get(family_id).targets(conn, "daily")
                    else:
                        targets = data_registry.get(family_id).targets(
                            conn,
                            "daily",
                            facts=facts,
                            universe_loader=lambda members=tuple(loaded): SimpleNamespace(tickers=members),
                        )
            except core_tx.Unavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - 目标都解析不出来：这一族自己的失败行
                tally.record("*", exc)
                continue
            tally.set_targets(targets)
            _run_family(
                family_id,
                targets,
                session=session,
                now=now,
                run_id=run_id,
                trigger=trigger,
                cfg=cfg,
                deps=deps,
                tally=tally,
                scope="daily",
                observe=observed_universe if family_id == "universe" else None,
            )
            if family_id == "universe" and not loaded:
                try:
                    loaded = list(load_universe().tickers)
                except ValueError:
                    loaded = []
    rows = [
        tallies[family_id].row(
            trigger=trigger,
            run_id=run_id,
            session=session,
            started_at=started_at,
            finished_at=data_collect.wall_clock(),
            scope="daily",
        )
        for family_id in DAILY_FAMILIES
    ]
    data_collect.write_runs_with_ids(rows)
    return rows


def collect_extra(
    tickers: list[str],
    *,
    session: date,
    now: datetime,
    run_id: str,
    trigger: str,
    cfg: AssistantConfig,
    families: tuple[str, ...] = EXTRA_FAMILIES,
    deps: AdapterDeps | None = None,
) -> list[dict]:
    """The named families for exactly these tickers (screen-backfill scope); empty input runs nothing."""
    if not tickers:
        return []
    deps = deps if deps is not None else _default_deps(cfg)
    started_at = data_collect.wall_clock()
    tallies: list[data_collect.Tally] = []
    with data_collect.family_locks(_locked(families)):
        for family_id in families:
            tally = data_collect.Tally(family_id, "v3")
            tallies.append(tally)
            try:
                with core_tx._tx() as conn:
                    targets = data_registry.get(family_id).targets(conn, "screen_new_members", explicit=tickers)
            except core_tx.Unavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - 目标解析失败：这一族自己的失败行
                tally.record("*", exc)
                continue
            tally.set_targets(targets)
            _run_family(
                family_id,
                targets,
                session=session,
                now=now,
                run_id=run_id,
                trigger=trigger,
                cfg=cfg,
                deps=deps,
                tally=tally,
                scope="screen_new_members",
            )
    rows = [
        tally.row(
            trigger=trigger,
            run_id=run_id,
            session=session,
            started_at=started_at,
            finished_at=data_collect.wall_clock(),
            scope="screen_new_members",
        )
        for tally in tallies
    ]
    data_collect.write_runs_with_ids(rows)
    return rows
