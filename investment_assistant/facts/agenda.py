"""The day's agenda (spec §9.3): holdings reviews, relisted pending plans, the confirmation
queue with slot and replacement rules (spec §6.4), and whether the day is complete. Reads
only; the agent works through it, the host judges what belongs on it."""

from __future__ import annotations

from datetime import date

from investment_assistant.config import AssistantConfig
from investment_assistant.execution import plan_line
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts import repo as facts_repo

RELISTED_DONE_EVENTS = ("continued", "revised", "invalidated")
RESEARCH_DONE_EVENTS = ("concluded", "rejected", "planned")


def agenda(conn, *, session: date, cfg: AssistantConfig) -> dict:
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
    free = plan_line.free_slots(pending_count=len(rule_pending), cap=cfg.v3.pending_cap)
    replacement = None
    confirmations: list[dict] = []
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
                    "done": row["ticker"] in verdicts,
                }
            )
    else:
        replaced_today = any(row["event"] == "replaced" for row in events)
        replacement = plan_line.replacement(
            top_rows=[row for row in top_rows if row["ticker"] not in watching],  # 不做替换候选（R6）
            pending=rule_pending,
            active_tickers=active_tickers,
            replaced_today=replaced_today,
        )
        if replacement is not None:
            candidate = replacement["candidate"]
            confirmations = [
                {
                    "ticker": candidate,
                    "rank": rank_by_ticker[candidate],
                    "mode": "replace",
                    "victim_plan_id": replacement["victim_plan_id"],
                    "done": candidate in verdicts,
                }
            ]

    research_done = {
        row["research_id"]
        for row in research_repo.research_events_on(conn, session)
        if row["source"] == "agent" and row["event"] in RESEARCH_DONE_EVENTS
    }
    research = [
        {"research_id": row["id"], "ticker": row["ticker"], "done": row["id"] in research_done}
        for row in research_repo.research_by_status(conn, ("watching",))
        if row["triggered_session"] is not None
    ]

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
        "replacement": replacement,
        "complete": complete,
    }
