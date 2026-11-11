"""GET /api/today：今日操作与账户条（spec §12）。由当前状态直接算；没有运行记录时页面照常
打开（session=null、actions=[]），账户条仍按已有账本显示。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter

from investment_assistant.api import deps
from investment_assistant.config import AssistantConfig, load_config
from investment_assistant.core.texts import money_value
from investment_assistant.facts import plan_repo, research_repo
from investment_assistant.facts.account import account_view
from investment_assistant.facts.rotation import exposure_row
from investment_assistant.facts.view import today_actions

router = APIRouter(prefix="/api")


def _account_row(conn, *, session: date | None, cutoff_at: datetime, cfg: AssistantConfig) -> dict:
    """账户条的七个字段（接口契约表，v3.3 加 exposure）：金额 Decimal，序列化成字符串。没有运行记录时
    session 为 None——read_bars 的 end_date 与 NULL 比较恒为假，等于没有收盘价。"""
    ledger = account_view(
        conn,
        session=session,  # type: ignore[arg-type]  # None 即“不取日线”
        cutoff_at=cutoff_at,  # type: ignore[arg-type]  # None 即“不取日线”
    )["ledger"]
    unrealized = sum(
        (row["unrealized_pnl"] for row in ledger["positions"] if row["unrealized_pnl"] is not None), Decimal(0)
    )
    return {  # 页面原样显示这些字符串：金额取到分（core.texts.money_value）
        "cash": money_value(ledger["cash"]),
        "equity": money_value(ledger["equity"]),
        "unrealized_pnl": money_value(unrealized),
        "realized_pnl": money_value(ledger["realized_pnl"]),
        "cash_negative": ledger["cash_negative"],
        "notes": ledger["notes"],
        "exposure": deps.exposure_payload(
            exposure_row(conn, session=session, cutoff_at=cutoff_at, cfg=cfg)  # type: ignore[arg-type]  # None 即“不取日线”
        ),
    }


def _counts(conn, actions: list) -> dict:
    return {
        "actions": len(actions),
        "holdings": len(plan_repo.plans_by_status(conn, ("open",))),
        "pending": len(plan_repo.plans_by_status(conn, ("pending",))),
        "watching": len(research_repo.research_by_status(conn, ("watching",))),
    }


@router.get("/today")
def get_today():
    now = datetime.now(UTC)
    try:
        env = deps.web_env(now=now)
    except ValueError:  # no_run：页面照常打开，没有运行记录时今日操作为空
        return deps.ok(
            deps.read_tx(
                lambda conn: {
                    "session": None,
                    "cutoff_at": now,
                    "actions": [],
                    "counts": _counts(conn, []),
                    "account": _account_row(conn, session=None, cutoff_at=now, cfg=load_config()),
                }
            )
        )

    def _read(conn):
        actions = today_actions(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg)
        return {
            "session": env.session,
            "cutoff_at": env.cutoff_at,
            "actions": actions,
            "counts": _counts(conn, actions),
            "account": _account_row(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg),
        }

    return deps.ok(env.read(_read))
