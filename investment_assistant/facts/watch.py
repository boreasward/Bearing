"""Market watch rows from active plans and watching research, at a fixed data cutoff."""

from datetime import date, datetime
from decimal import Decimal

from investment_assistant.data import repo as data_repo
from investment_assistant.facts import plan_repo, research_repo


def watch_rows(conn, *, session: date, cutoff_at: datetime) -> list[dict]:
    rows = []
    seen = set()
    groups = (
        ("holding", plan_repo.plans_by_status(conn, ("open",))),
        ("pending", plan_repo.plans_by_status(conn, ("pending",))),
        ("research", research_repo.research_by_status(conn, ("watching",))),
    )
    for source, objects in groups:
        for obj in objects:
            ticker = obj["ticker"]
            if ticker in seen:
                continue
            seen.add(ticker)
            bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at, limit=2)
            last = prev = change = None
            if len(bars) == 2:
                last, prev = (Decimal(str(bar["close"])) for bar in reversed(bars))
                if prev:
                    change = f"{(last - prev) / prev * 100:.2f}"
            rows.append(
                {
                    "ticker": ticker,
                    "source": source,
                    "plan_id": obj["id"] if source != "research" else None,
                    "research_id": obj["id"] if source == "research" else None,
                    "last_close": str(last) if last is not None else None,
                    "prev_close": str(prev) if prev is not None else None,
                    "change_pct": change,
                }
            )
    return rows
