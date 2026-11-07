"""v3 draft rendering: a thin delegate to facts.brief (v3.1 spec §11).

The draft is the brief without the agent parts; `render_draft` keeps its name and signature
so job.py works unchanged. Task 3 rewires job.py to facts.view + render_brief directly and
then deletes or shrinks this module to the error brief.
"""

from __future__ import annotations

from datetime import date, datetime

from investment_assistant.facts.brief import render_brief, render_error_brief


def render_draft(
    *,
    session: date,
    as_of: datetime,
    market: dict,
    candidates: list[dict],
    holdings: list[dict],
    coverage: dict,
    top_n: int,
    error: str | None = None,
) -> str:
    """The draft of the session (spec §11 minus the agent parts); an `error` draft carries
    only the title, the reason and the footer."""
    if error is not None:
        return render_error_brief(session=session, as_of=as_of, error=error, has_holdings=bool(holdings))
    view = {
        "session": session,
        "as_of": as_of,
        "top_n": top_n,
        "first_run": False,
        "today_actions": [],
        "summary": None,
        "changes": None,
        "market": market,
        "holdings": holdings,
        "pending_plans": [],
        "candidates": candidates,
        "dropped": [],
        "account": None,
        "coverage": coverage,
        "unresolved": {"missing_bars": [], "agenda": []},
        "footer": {},
    }
    return render_brief(view, final=False)
