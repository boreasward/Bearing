"""Everything the brief needs, assembled from the database (spec §11): the ``view`` dict that
``facts.brief.render_brief`` consumes, plus ``today_actions`` shared by the brief, the page
and the stop-order discipline. Host facts plus whatever the agent already saved today — no
model, no rendering here.

``run`` is the day's run row (or the dict the job passes before inserting it): it must carry
``market``, ``coverage`` and ``dropped``, and may carry ``candidates`` (the job does; later
callers' rows don't, in which case they are read from the run persisted for the session)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from investment_assistant.config import AssistantConfig
from investment_assistant.core.texts import quantity_value
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import trading_days_between
from investment_assistant.execution.plan_levels import _cent
from investment_assistant.execution.plan_line import held_sessions, next_action
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.account import account_view
from investment_assistant.facts.agenda import agenda
from investment_assistant.facts.continuity import continuity
from investment_assistant.facts.holdings import holdings_view

_BEIJING = ZoneInfo("Asia/Shanghai")

# spec §7.4 的次序：第 1–4 条进「今日操作」，第 5 条（继续持有）不进
ACTION_ORDER = {"stop_hit_unresolved": 0, "sell_next_open": 1, "unchecked": 2, "stop_order": 3}
ENDED_EVENTS = ("expired", "invalidated", "replaced", "cancelled", "closed")
HOLDING_EVENTS = ("stop_hit", "target_hit", "stop_raised", "sell_instruction")
RESEARCH_CONCLUSION_EVENTS = ("concluded", "rejected", "planned")  # 当天结论句取它们的 reason（spec §8 第 5a 段）
NO_STOP_TEXT = "缺日线，未能给出止损"  # 补录计划还没算出止损（spec §6.6）


def plan_actions(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig) -> list[dict]:
    """每只未平仓持仓「下一个交易日的操作」全表（spec §7.4 五条，含第 5 条「继续持有」）。

    简报第 4 段、Discord 推送与 get_positions 的持仓行都取这张全表；「今日操作」（第 0 段）
    用 :func:`today_actions` 的过滤视图。"""
    del cfg  # 目前无需配置；签名留给页面与简报共用
    by_ticker: dict[str, list[dict]] = {}
    for trade in facts_repo.list_trades(conn):
        by_ticker.setdefault(trade["ticker"], []).append(trade)
    actions: list[dict] = []
    for plan in plan_repo.plans_by_status(conn, ("open",)):
        ticker = plan["ticker"]
        if ticker not in by_ticker:
            continue
        splits = data_repo.split_events(conn, ticker, cutoff_at=cutoff_at)
        try:
            position = build(by_ticker[ticker], splits=splits)["position"]
        except PositionError:
            continue  # 无法核算的持仓不进今日操作
        if position is None:
            continue
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
        unchecked = not bars or bars[-1]["price_date"] != session
        if plan["stop"] is None:
            action = {"kind": "unchecked", "text": NO_STOP_TEXT, "stop": None, "reason": None}
        else:
            # 给用户看的止损价取到分：库里 NUMERIC(18,6) 的 98.000000 应显示为 98.00
            shown = dict(plan)
            shown["stop"] = _cent(plan["stop"])
            if shown.get("confirmed_stop_order") is not None:
                shown["confirmed_stop_order"] = _cent(shown["confirmed_stop_order"])
            action = next_action(plan=shown, position=position, unchecked=unchecked)
        actions.append(
            {
                "plan_id": plan["id"],
                "ticker": ticker,
                "kind": action["kind"],
                "text": action["text"],
                "stop": action["stop"],
                "quantity": quantity_value(position["quantity"]),
                "reason": action["reason"],
            }
        )
    actions.sort(key=lambda row: ACTION_ORDER.get(row["kind"], len(ACTION_ORDER)))
    return actions


def today_actions(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig) -> list[dict]:
    """每只未平仓持仓「下一个交易日的操作」（spec §7.4 第 1–4 条），按规则次序排序。

    第 5 条（继续持有）不进「今日操作」（spec §11 第 0 段），但持仓行仍带它（第 4 段）。"""
    return [row for row in plan_actions(conn, session=session, cutoff_at=cutoff_at, cfg=cfg) if row["kind"] != "hold"]


def actions_by_ticker(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig) -> dict[str, dict]:
    """持仓行的操作表（含「继续持有」）：ticker -> {kind, text, stop, reason}。"""
    return {
        row["ticker"]: {"kind": row["kind"], "text": row["text"], "stop": row["stop"], "reason": row["reason"]}
        for row in plan_actions(conn, session=session, cutoff_at=cutoff_at, cfg=cfg)
    }


def _changes(conn, session: date) -> dict:
    """当天事件拼成「和昨天比变了什么」的三组句子（spec §11 第 2 段）。"""
    plan_tickers: dict[int, str] = {}

    def ticker_of(plan_id: int) -> str:
        if plan_id not in plan_tickers:
            row = plan_repo.plan(conn, plan_id)
            plan_tickers[plan_id] = row["ticker"] if row is not None else "?"
        return plan_tickers[plan_id]

    new_plans, ended_plans, holding_changes = [], [], []
    for event in plan_repo.events_on(conn, session):
        ticker = ticker_of(event["plan_id"])
        if event["event"] in ("created", "adopted"):
            new_plans.append(f"新计划 {ticker}：{event['reason']}")
        elif event["event"] in ENDED_EVENTS:
            ended_plans.append(f"结束 {ticker}：{event['reason']}")
        elif event["event"] in HOLDING_EVENTS:
            holding_changes.append(f"{ticker} {event['reason']}")
    return {"new_plans": new_plans, "ended_plans": ended_plans, "holding_changes": holding_changes}


def _candidate_rows(
    conn,
    *,
    session: date,
    cfg: AssistantConfig,
    rows: list[dict],
    streak: dict[str, int],
    new: set[str],
    active_by_ticker: dict[str, dict],
    day: dict,
    watching: set[str],
) -> list[dict]:
    verdicts = day["verdicts"]
    replacement_candidate = (day["agenda"].get("replacement") or {}).get("candidate")
    out = []
    for row in rows:
        ticker = row["ticker"]
        plan = active_by_ticker.get(ticker)
        held = plan is not None and plan["status"] == "open"
        has_research = ticker in watching
        skip_reason = None
        if has_research:
            skip_reason = "研究中"  # 结论走研究那条线，优先于其他原因（spec §6.6 / R6）
        elif plan is not None and plan["status"] == "pending":
            skip_reason = "已有计划"
        elif row["rank"] <= cfg.v3.top_n and day["agenda"]["free_slots"] == 0 and ticker != replacement_candidate:
            skip_reason = "名额已满"
        out.append(
            {
                "ticker": ticker,
                "rank": row["rank"],
                "reason": row["reason"],
                "streak": streak.get(ticker),
                "new": ticker in new,
                "verdict": verdicts[ticker]["verdict"] if ticker in verdicts else None,
                "has_active_plan": plan is not None,
                "plan_id": plan["id"] if plan is not None else None,
                "has_research": has_research,
                "skip_reason": skip_reason,
                "held": held,
            }
        )
    return out


def brief_view(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, run: dict) -> dict:
    """``render_brief`` 的全部输入；agent 已保存的结论（复查、verdict、一句话）一并带入。"""
    run = run or {}
    market = run.get("market") or {}
    cov = run.get("coverage") or {}
    dropped = list(run.get("dropped") or [])
    rows = run.get("candidates")
    if rows is None:
        latest = facts_repo.latest_run(conn, session)
        rows = facts_repo.candidates(conn, latest["run_id"]) if latest is not None else []

    previous_run = facts_repo.previous_ok_run(conn, before=session)
    previous_rows = None
    if previous_run is not None:
        previous_rows = [
            {"ticker": row["ticker"], "rank": row["rank"], "streak": (row.get("detail") or {}).get("streak", 1)}
            for row in facts_repo.candidates(conn, previous_run["run_id"])
        ]
    cont = continuity(
        today=[{"ticker": row["ticker"], "rank": row["rank"]} for row in rows],
        previous=previous_rows,
        screen_results=[],
        skips={},
    )

    active = plan_repo.plans_by_status(conn, ("pending", "open"))
    active_by_ticker = {plan["ticker"]: plan for plan in active}
    trades = facts_repo.list_trades(conn)
    closes: dict[str, float] = {}
    splits: dict[str, list] = {}
    for ticker in sorted({trade["ticker"] for trade in trades}):
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
        if bars:
            closes[ticker] = bars[-1]["close"]
        splits[ticker] = data_repo.split_events(conn, ticker, cutoff_at=cutoff_at)
    plans_map = {}
    for plan in active:
        row = dict(plan)
        row["held_sessions"] = (
            held_sessions(plan["start_session"], session)
            if plan["status"] == "open" and plan["start_session"]
            else None
        )
        plans_map[plan["ticker"]] = row
    actions = actions_by_ticker(conn, session=session, cutoff_at=cutoff_at, cfg=cfg)
    holdings = holdings_view(trades, closes=closes, plans=plans_map, splits=splits, actions=actions)
    reviews = plan_repo.holding_reviews(conn, session)
    plans_by_id = {plan["id"]: plan for plan in active}
    for row in holdings:
        plan = plans_by_id.get(row.get("plan_id"))
        if plan is None:
            continue
        if plan["id"] in reviews:
            review = reviews[plan["id"]]
            row["review"] = {"verdict": review["verdict"], "reasons": review["reasons"]}
        earnings = data_repo.latest_earnings(conn, plan["ticker"], cutoff_at=cutoff_at)
        if earnings is not None and earnings.get("status") == "known" and earnings.get("earnings_date"):
            row["earnings_note"] = f"财报日 {earnings['earnings_date']:%m-%d}"

    account = account_view(conn, session=session, cutoff_at=cutoff_at)
    ledger = account["ledger"]
    has_account = ledger["cash"] is not None or ledger["positions"] or ledger["realized_pnl"]
    unrealized = sum(
        (row["unrealized_pnl"] for row in ledger["positions"] if row["unrealized_pnl"] is not None), Decimal(0)
    )
    account_row = (
        {
            "cash": ledger["cash"],
            "equity": ledger["equity"],
            "unrealized_pnl": unrealized,
            "realized_pnl": ledger["realized_pnl"],
            "cash_negative": ledger["cash_negative"],
            "notes": ledger["notes"],
        }
        if has_account
        else None
    )

    day_agenda = agenda(conn, session=session, cfg=cfg)
    watching_research = research_repo.research_by_status(conn, ("watching",))
    watching = {row["ticker"] for row in watching_research}
    research_events_today = research_repo.research_events_on(conn, session)
    unresolved_items = []
    unresolved_items += [f"持仓复查 {item['ticker']}" for item in day_agenda["holdings"] if not item["done"]]
    unresolved_items += [f"再次上榜计划 {item['ticker']} 的结论" for item in day_agenda["relisted"] if not item["done"]]
    unresolved_items += [f"二次确认 {item['ticker']}" for item in day_agenda["confirmations"] if not item["done"]]
    unresolved_items += [f"研究 {row['ticker']} 尚无条件" for row in watching_research if not row["condition"]]
    unresolved_items += [f"研究复看 {item['ticker']}" for item in day_agenda["research"] if not item["done"]]
    if not day_agenda["summary_done"]:
        unresolved_items.append("一句话总结")

    brief_row = plan_repo.brief(conn, session)
    # 待入场计划的当日缺日线标志（spec §6.3 末段）：与 today_actions / 计划卡片同一判定
    # （bars[-1]["price_date"] != session），保证卡片与简报不会各说各话。
    pending_plans = []
    for plan in active:
        if plan["status"] != "pending":
            continue
        bars = data_repo.read_bars(conn, plan["ticker"], session, cutoff_at=cutoff_at)
        pending_plans.append(
            {
                "ticker": plan["ticker"],
                "day": len(trading_days_between(plan["session"], session)),  # 两端都算：保存当天是第 1 天
                "entry_low": plan["entry_low"],
                "entry_high": plan["entry_high"],
                "stop": plan["stop"],
                "target1": plan["target1"],
                "shares": plan["shares"],
                "valid_until": plan["valid_until"],
                "missing_bar": not bars or bars[-1]["price_date"] != session,
            }
        )
    changes = _changes(conn, session)
    changes["new_count"] = len(cont["new"])
    changes["dropped_count"] = len(dropped)

    # 第 5a 段「研究」（spec §8）：观察中的研究各一行，状态按 triggered → 尚无条件 → 缺日线 → 观察中
    conclusions = {
        event["research_id"]: event["reason"]
        for event in research_events_today
        if event["event"] in RESEARCH_CONCLUSION_EVENTS
    }
    research_rows = []
    for row in watching_research:
        bars = data_repo.read_bars(conn, row["ticker"], session, cutoff_at=cutoff_at)
        if row["triggered_session"] is not None:
            state = "triggered"
        elif not row["condition"]:
            state = "no_condition"
        elif not bars or bars[-1]["price_date"] != session:
            state = "missing_bar"
        else:
            state = "watching"
        research_rows.append(
            {
                "research_id": row["id"],
                "ticker": row["ticker"],
                "day": len(trading_days_between(row["created_session"], session)),  # 两端都算：发起当天是第 1 天
                "thesis": row["thesis"],
                "condition_text": row["condition_text"],
                "last_close": bars[-1]["close"] if bars else None,
                "state": state,
                "conclusion": conclusions.get(row["id"]),
            }
        )
    research_tickers: dict[int, str] = {}

    def _research_ticker(research_id: int) -> str:
        """事件当天的研究可能已终态：按编号现查，缓存一次。"""
        if research_id not in research_tickers:
            found = research_repo.research(conn, research_id)
            research_tickers[research_id] = found["ticker"] if found is not None else "?"
        return research_tickers[research_id]

    changes["research"] = [
        f"研究 {_research_ticker(event['research_id'])}：{event['reason']}" for event in research_events_today
    ]
    footer = (
        {
            "runtime": brief_row["runtime"],
            "provider": brief_row["provider"],
            "model": brief_row["model"],
            "usage": brief_row["usage"] or {},
            "elapsed_seconds": brief_row["elapsed_seconds"],
        }
        if brief_row is not None
        else {}
    )
    return {
        "session": session,
        "as_of": cutoff_at.astimezone(_BEIJING),
        "error": None,
        "today_actions": today_actions(conn, session=session, cutoff_at=cutoff_at, cfg=cfg),
        "changes": changes,
        "market": market,
        "holdings": holdings,
        "pending_plans": pending_plans,
        "research": research_rows,
        "candidates": _candidate_rows(
            conn,
            session=session,
            cfg=cfg,
            rows=rows,
            streak=cont["streak"],
            new=set(cont["new"]),
            active_by_ticker=active_by_ticker,
            day={"verdicts": facts_repo.verdicts(conn, session), "agenda": day_agenda},
            watching=watching,
        ),
        "dropped": dropped,
        "account": account_row,
        "coverage": cov,
        "unresolved": {"missing_bars": list(cov.get("missing_bars") or []), "agenda": unresolved_items},
        "footer": footer,
        "summary": brief_row["summary"] if brief_row is not None else None,
        "first_run": previous_run is None,
        "top_n": cfg.v3.top_n,
    }
