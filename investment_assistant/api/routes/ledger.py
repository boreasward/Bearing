"""账本接口：POST /api/trades|cash-flows|reconcile|voids 与 GET /api/account（spec §12 账户条）。
写入口只转发给工具面的同名宿主函数：同一套复述、confirmed 与 request_id 幂等（spec §10 末段）。"""

from __future__ import annotations

from fastapi import APIRouter, Body

from investment_assistant.api import deps
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.account import account_view
from investment_assistant.toolsurface import chat as chat_tools
from investment_assistant.toolsurface import ledger as ledger_tools

router = APIRouter(prefix="/api")

BODY = Body(...)  # 模块级单例：JSON 请求体（ruff B008）


@router.post("/trades")
def record_trade(body: dict = BODY):
    return deps.ok_or_error(lambda: deps.forward(chat_tools.record_trade, deps.web_env(), body))


@router.post("/cash-flows")
def record_cash_flow(body: dict = BODY):
    return deps.ok_or_error(lambda: deps.forward(ledger_tools.record_cash_flow, deps.web_env(), body))


@router.post("/reconcile")
def reconcile(body: dict = BODY):
    return deps.ok_or_error(lambda: deps.forward(ledger_tools.record_equity, deps.web_env(), body))


@router.post("/voids")
def void_record(body: dict = BODY):
    return deps.ok_or_error(lambda: deps.forward(ledger_tools.void_record, deps.web_env(), body))


@router.get("/account")
def account():
    def _read(env):
        return env.read(lambda conn: account_view(conn, session=env.session, cutoff_at=env.cutoff_at))

    return deps.ok_or_error(lambda: _read(deps.web_env()))


@router.get("/ledger")
def ledger(limit: int = 20):
    def _read():
        if not 1 <= limit <= 100:
            raise ValueError("invalid_argument: limit 必须是 1–100 之间的整数")
        tables = {
            "trades": (
                facts_repo.recent_trades,
                (
                    "id",
                    "ticker",
                    "side",
                    "quantity",
                    "price",
                    "fee",
                    "executed_at",
                    "plan_id",
                    "note",
                    "request_id",
                    "voided_by",
                    "void_reason",
                ),
            ),
            "cash_flows": (
                plan_repo.recent_cash_flows,
                ("id", "kind", "amount", "effective_at", "note", "voided_by", "void_reason"),
            ),
            "equity": (facts_repo.recent_equity, ("id", "as_of", "equity", "note")),
            "voids": (plan_repo.recent_voids, ("id", "kind", "target_id", "reason", "created_at")),
        }
        return deps.read_tx(
            lambda conn: {
                name: [{field: row[field] for field in fields} for row in query(conn, limit=limit)]
                for name, (query, fields) in tables.items()
            }
        )

    return deps.ok_or_error(_read)
