"""The day's agenda (spec §9.3): holdings reviews, relisted pending plans, the confirmation
queue with slot and replacement rules (spec §6.4), and whether the day is complete. Reads
only; the agent works through it, the host judges what belongs on it."""

from __future__ import annotations

from datetime import UTC, date, datetime

from investment_assistant.config import AssistantConfig
from investment_assistant.execution.plan_line import free_slots, replacement
from investment_assistant.facts import plan_repo, research_repo, rotation_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import rotation as facts_rotation

RELISTED_DONE_EVENTS = ("continued", "revised", "invalidated")
RESEARCH_DONE_EVENTS = ("concluded", "rejected", "planned")


def _rotation_mark(conn, ticker: str, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, verdicts: dict, recorded: set) -> dict:
    """确认项的换仓标记（spec §6.1）与它的完成判定：钱够照旧（当天有结论）；钱不够而不比较算完成；
    钱不够且比较：当天结论不是确认，或当天已有这只股票的换仓记录。"""
    gate = facts_rotation.gate(conn, session=session, cutoff_at=cutoff_at, cfg=cfg, ticker=ticker, mode="brief")
    mark = {key: gate[key] for key in ("short", "compare", "blocked", "blocked_text")}
    if not gate["short"]:
        done = ticker in verdicts
    elif not gate["compare"]:
        done = True
    else:
        verdict = verdicts.get(ticker)
        done = (verdict is not None and verdict["verdict"] != "confirm") or ticker in recorded
    return {"rotation": mark, "done": done}


def agenda(conn, *, session: date, cfg: AssistantConfig, cutoff_at: datetime | None = None) -> dict:
    """``cutoff_at`` 只用来读日线判换仓闸门；缺省取当前时刻（每日会话里与数据入库时点无冲突）。"""
    cutoff_at = cutoff_at if cutoff_at is not None else datetime.now(UTC)
    run = facts_repo.latest_run(conn, session)
    top_rows = []
    if run is not None:
        top_rows = [row for row in facts_repo.candidates(conn, run["run_id"]) if row["rank"] <= cfg.v3.top_n]
    rank_by_ticker = {row["ticker"]: row["rank"] for row in top_rows}
    active = plan_repo.plans_by_status(conn, ("pending", "open"))
    open_plans = [plan for plan in active if plan["status"] == "open"]
    pending = [plan for plan in active if plan["status"] == "pending"]
    rule_pending = [plan for plan in pending if plan["research_id"] is None]  # 名额只约束规则计划（spec §6.6）
    reviews = plan_repo.holding_reviews(conn, session)
    events = plan_repo.events_on(conn, session)
    verdicts = facts_repo.verdicts(conn, session)

    holdings = [{"plan_id": plan["id"], "ticker": plan["ticker"], "done": plan["id"] in reviews} for plan in open_plans]
    concluded_today = {row["plan_id"] for row in events if row["event"] in RELISTED_DONE_EVENTS}
    relisted = []
    for plan in pending:
        rank = rank_by_ticker.get(plan["ticker"])
        if rank is None or plan["session"] == session:
            # 当天刚保存的计划不是「再次上榜」：它的待办是当天的确认，已经做完
            continue
        verdict = verdicts.get(plan["ticker"])
        done = plan["id"] in concluded_today or (verdict is not None and verdict["verdict"] == "unknown")
        relisted.append({"plan_id": plan["id"], "ticker": plan["ticker"], "rank": rank, "done": done})

    active_tickers = {plan["ticker"] for plan in active}
    watching = set(research_repo.watching_tickers(conn))
    free = free_slots(pending_count=len(rule_pending), cap=cfg.v3.pending_cap)
    replacement_pick = None  # 本地名让位：replacement 是从 plan_line 导入的函数
    confirmations: list[dict] = []
    rotations_today = rotation_repo.rotations_on(conn, session)
    recorded = {row["candidate"] for row in rotations_today}

    def mark(ticker: str) -> dict:
        return _rotation_mark(
            conn, ticker, session=session, cutoff_at=cutoff_at, cfg=cfg, verdicts=verdicts, recorded=recorded
        )

    if free > 0:
        for row in top_rows:
            if row["ticker"] in active_tickers or row["ticker"] in watching:
                # 有观察中研究的股票不进确认清单（spec §6.6 / R6）：结论走研究那条线
                continue
            confirmations.append(
                {
                    "ticker": row["ticker"],
                    "rank": row["rank"],
                    "mode": "slot",
                    "victim_plan_id": None,
                    **mark(row["ticker"]),
                }
            )
    else:
        replaced_today = any(row["event"] == "replaced" for row in events)
        replacement_pick = replacement(
            top_rows=[row for row in top_rows if row["ticker"] not in watching],  # 不做替换候选（R6）
            pending=rule_pending,
            active_tickers=active_tickers,
            replaced_today=replaced_today,
        )
        if replacement_pick is not None:
            candidate = replacement_pick["candidate"]
            confirmations = [
                {
                    "ticker": candidate,
                    "rank": rank_by_ticker[candidate],
                    "mode": "replace",
                    "victim_plan_id": replacement_pick["victim_plan_id"],
                    **mark(candidate),
                }
            ]

    research_done = {
        row["research_id"]
        for row in research_repo.research_events_on(conn, session)
        if row["source"] == "agent" and row["event"] in RESEARCH_DONE_EVENTS
    }
    research = []
    for row in research_repo.research_by_status(conn, ("watching",)):
        if row["triggered_session"] is None:
            continue
        item = {"research_id": row["id"], "ticker": row["ticker"], "done": row["id"] in research_done}
        if item["done"] and row["verdict"] == "ready":
            # 当天得出「可以出计划」的研究 B 与确认项同样要过换仓闸门（spec §6.1 条件 1、§7.1）：
            # 钱不够且可比较时，完成要等到换仓比较有了着落；其余情况研究结论就是完成
            marked = mark(row["ticker"])
            needs_comparison = marked["rotation"]["short"] and marked["rotation"]["compare"]
            item.update(
                rotation=marked["rotation"],
                reviewed=True,  # 研究复看本身已做：未完成只欠换仓比较，不再算「研究复看」未做
                done=marked["done"] if needs_comparison else True,
            )
        research.append(item)

    brief_row = plan_repo.brief(conn, session)
    summary_done = brief_row is not None and bool(brief_row["summary"])
    complete = (
        all(item["done"] for item in holdings)
        and all(item["done"] for item in relisted)
        and all(item["done"] for item in confirmations)
        and all(item["done"] for item in research)
        and summary_done
    )
    return {
        "holdings": holdings,
        "relisted": relisted,
        "confirmations": confirmations,
        "research": research,
        "summary_done": summary_done,
        "free_slots": free,
        "replacement": replacement_pick,
        "rotation": {
            "compared_today": any(row["origin"] == "brief" for row in rotations_today),
            "last_session": facts_rotation.cooldown_last_session(conn, session=session, cfg=cfg),
        },
        "complete": complete,
    }
