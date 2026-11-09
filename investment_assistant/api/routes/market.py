"""Latest successful market snapshot with the current watch list."""

from datetime import UTC, datetime

from fastapi import APIRouter

from investment_assistant.api import deps
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.watch import watch_rows

router = APIRouter(prefix="/api")


@router.get("/market")
def market():
    now = datetime.now(UTC)

    def _read(conn):
        run = facts_repo.latest_ok_run(conn)
        if run is None:
            raise ValueError("no_run")
        snapshot = run["market"] or {}
        return {
            **snapshot,
            "session": run["session"],
            "calc_version": snapshot.get("calc_version"),
            "watch": watch_rows(conn, session=run["session"], cutoff_at=now),
        }

    return deps.ok_or_error(lambda: deps.read_tx(_read))
