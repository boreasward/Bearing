"""Fact run history joined to brief outcomes and the configured Beijing schedule."""

from datetime import UTC, datetime

from fastapi import APIRouter

from investment_assistant.api import deps
from investment_assistant.config import load_config
from investment_assistant.core.schedule import next_run_at
from investment_assistant.facts import repo as facts_repo

router = APIRouter(prefix="/api")
RUN_FIELDS = ("session", "trigger", "status", "error", "started_at", "finished_at")
BRIEF_FIELDS = (
    "status",
    "error",
    "runtime",
    "provider",
    "model",
    "usage",
    "elapsed_seconds",
    "pushed_final_at",
    "pushed_error_at",
)


def _run_row(row: dict) -> dict:
    brief = row["brief"]
    return {
        **{field: row[field] for field in RUN_FIELDS},
        "brief": None
        if brief is None
        else {
            **{field: brief[field] for field in BRIEF_FIELDS},
            "unfinished": brief["status"] != "final",
            "weekly": {"present": brief["weekly_present"], "pushed_at": brief["pushed_weekly_at"]},
        },
    }


@router.get("/runs")
def runs(limit: int = 20):
    def _read():
        if not 1 <= limit <= 100:
            raise ValueError("invalid_argument: limit 必须是 1–100 之间的整数")
        cfg = load_config().v3
        return {
            "schedule": {
                "weekdays": cfg.daily_weekdays,
                "hour": cfg.daily_hour,
                "minute": cfg.daily_minute,
                "tz": "Asia/Shanghai",
                "next_at": next_run_at(
                    datetime.now(UTC), weekdays=cfg.daily_weekdays, hour=cfg.daily_hour, minute=cfg.daily_minute
                ),
            },
            "runs": deps.read_tx(
                lambda conn: [_run_row(row) for row in facts_repo.runs_with_briefs(conn, limit=limit)]
            ),
        }

    return deps.ok_or_error(_read)
