"""GET /api/briefs*：简报列表与单份简报（spec §12 今日简报块）；没有 → 404 no_brief。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from investment_assistant.api import deps
from investment_assistant.facts import plan_repo

router = APIRouter(prefix="/api/briefs")


def _limit_or_response(limit: int):
    if not 1 <= limit <= 100:
        return JSONResponse({"error": "invalid_argument: limit 必须是 1–100 之间的整数"}, status_code=400)
    return None


def _brief_row(row: dict) -> dict:
    return {
        "session": row["session"],
        "status": row["status"],
        "markdown": row["final_md"] if row["final_md"] is not None else row["draft_md"],
        "error": row["error"],
        "runtime": row["runtime"],
        "provider": row["provider"],
        "model": row["model"],
        "usage": row["usage"] or {},
        "elapsed_seconds": row["elapsed_seconds"],
        "unfinished": row["status"] != "final",
    }


@router.get("")
def list_briefs(limit: int = 30):
    if bad := _limit_or_response(limit):
        return bad
    rows = deps.read_tx(lambda conn: plan_repo.briefs(conn, limit=limit))
    return deps.ok(
        {
            "briefs": [
                {
                    "session": row["session"],
                    "status": row["status"],
                    "pushed": row["pushed_final_at"] is not None or row["pushed_error_at"] is not None,
                }
                for row in rows
            ]
        }
    )


@router.get("/latest")
def latest_brief():
    def _read(conn):
        rows = plan_repo.briefs(conn, limit=1)
        if not rows:
            raise ValueError("no_brief")
        return _brief_row(rows[0])

    return deps.ok_or_error(lambda: deps.read_tx(_read))


@router.get("/{session}")
def brief_by_session(session: date):
    def _read(conn):
        row = plan_repo.brief(conn, session)
        if row is None:
            raise ValueError("no_brief")
        return _brief_row(row)

    return deps.ok_or_error(lambda: deps.read_tx(_read))
