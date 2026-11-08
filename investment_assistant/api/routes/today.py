"""GET /api/today：今日操作与账户条（spec §12）。由当前状态直接算；没有运行记录时页面照常
打开（session=null、actions=[]），账户条仍按已有账本显示。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from investment_assistant.core.texts import money_value

from fastapi import APIRouter

from investment_assistant.api import deps
from investment_assistant.facts.account import account_view
from investment_assistant.facts.view import today_actions

router = APIRouter(prefix="/api")


def _account_row(conn, *, session: date | None, cutoff_at: datetime) -> dict:
    """账户条的六个字段（接口契约表）：金额 Decimal，序列化成字符串。没有运行记录时
    session 为 None——read_bars 的 end_date 与 NULL 比较恒为假，等于没有收盘价。"""
    ledger = account_view(
        conn,
        session=session,
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
                    "account": _account_row(conn, session=None, cutoff_at=now),
                }
            )
        )

    def _read(conn):
        return {
            "session": env.session,
            "cutoff_at": env.cutoff_at,
            "actions": today_actions(conn, session=env.session, cutoff_at=env.cutoff_at, cfg=env.cfg),
            "account": _account_row(conn, session=env.session, cutoff_at=env.cutoff_at),
        }

    return deps.ok(env.read(_read))
