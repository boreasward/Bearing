from __future__ import annotations

from datetime import date, datetime
from typing import Any

from investment_assistant.core import tx as core_tx
from investment_assistant.analysis import repo as analysis_repo, screening

def screen_view(*, now: datetime, session: date | None = None, full: bool = False) -> dict[str, Any]:
    """The screen report of one session (default the newest run, spec §9.2); None when no run exists.

    The newest run, not the latest closed session: between the New York close and the
    batch the closed session has no run yet, and the page should keep showing the last one."""
    with core_tx._tx() as conn:
        if session is None:
            run = analysis_repo.latest_screen_run(conn, session=None)
            if run is None:
                return {'report': None}
            session = run['session']
        return {'report': screening.report(conn, session, now=now, full=full)}

def replay_screen(*, now: datetime, sessions: int) -> dict[str, Any]:
    return {'reports': screening.replay(sessions=sessions, now=now)}
