"""Tool registry and call dispatch: schema checks, error envelopes, one v3_tool_calls row per call."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import investment_assistant.core.tx as core_tx
from investment_assistant.core.tx import _jsonable
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface import chat as chat_tools
from investment_assistant.toolsurface import ledger as ledger_tools
from investment_assistant.toolsurface import planning, reads
from investment_assistant.toolsurface import plans as plan_tools
from investment_assistant.toolsurface import research as research_tools
from investment_assistant.toolsurface import watchlist as watchlist_tools
from investment_assistant.toolsurface.env import ToolEnv

BOTH = ("brief", "chat")
CHAT_ONLY = ("chat",)
RECORD_FAILURE_REF = "call:0"  # 调用记录写不进去时的 ref；seq 从 1 起，永不相撞

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": {"type": "string"}}


def _schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


@dataclass(frozen=True)
class ToolDef:
    name: str
    fn: Callable[..., Any]
    modes: tuple[str, ...]
    description: str
    input_schema: dict


_DEFS: tuple[ToolDef, ...] = (
    ToolDef(
        "get_market",
        reads.get_market,
        BOTH,
        "读取当日市场环境：区制标签与档位、广度、SPY 20 日收益、行业 ETF 强弱表与宏观指标；每天看盘先调它。",
        _schema({}),
    ),
    ToolDef(
        "get_candidates",
        reads.get_candidates,
        BOTH,
        "读取当日规则名单：每只的排名、理由行、形态与行业档位、相对强度、连续上榜天数、"
        "是否前 N 只、已有结论与是否有有效计划；末尾列当天掉出名单的股票及原因。",
        _schema({}),
    ),
    ToolDef(
        "get_coverage",
        reads.get_coverage,
        BOTH,
        "读取当日各取数族的覆盖率与缺日线清单，判断数据是否够用。",
        _schema({}),
    ),
    ToolDef(
        "get_price_bars",
        reads.get_price_bars,
        BOTH,
        "读取某只股票到当日为止的最近 lookback 根日线（开高低收与成交量）。",
        _schema({"ticker": _STRING, "lookback": {"type": "integer", "default": 60}}, ("ticker",)),
    ),
    ToolDef(
        "get_indicators",
        reads.get_indicators,
        BOTH,
        "读取某只股票的当日指标快照（收盘、均线、相对强度等）；没有日线时报 no_bars。",
        _schema({"ticker": _STRING}, ("ticker",)),
    ),
    ToolDef(
        "get_news",
        reads.get_news,
        BOTH,
        "读取某只股票最近 days 天的资讯，最多 20 条，按时间倒序。",
        _schema({"ticker": _STRING, "days": {"type": "integer", "default": 7}}, ("ticker",)),
    ),
    ToolDef(
        "get_filings",
        reads.get_filings,
        BOTH,
        "读取某只股票最近 days 天的 SEC 申报（类型、申报日、受理时间、条目），最多 20 条。",
        _schema({"ticker": _STRING, "days": {"type": "integer", "default": 30}}, ("ticker",)),
    ),
    ToolDef(
        "get_earnings",
        reads.get_earnings,
        BOTH,
        "读取某只股票下一次已知的财报日与状态；没有答案时返回 null。",
        _schema({"ticker": _STRING}, ("ticker",)),
    ),
    ToolDef(
        "get_fundamentals",
        reads.get_fundamentals,
        BOTH,
        "读取某只股票最近几个财季的 SEC 基本面数字（每条指标一行，最新在前），最多 40 行。",
        _schema({"ticker": _STRING}, ("ticker",)),
    ),
    ToolDef(
        "get_positions",
        reads.get_positions,
        BOTH,
        "读取当前持仓：每只的数量、成本、最新收盘、浮动盈亏、当前止损与移动止损状态、"
        "止损单状态、已持有交易日数与下一个交易日的操作。",
        _schema({}),
    ),
    ToolDef(
        "get_plans",
        reads.get_plans,
        BOTH,
        "读取计划：缺省返回全部有效计划；可按状态（pending/open/closed/expired/invalidated/"
        "replaced/cancelled）、股票或计划编号过滤，每份带它的变化记录时间线。",
        _schema({"status": _STRING, "ticker": _STRING, "plan_id": {"type": "integer"}}),
    ),
    ToolDef(
        "get_account",
        ledger_tools.get_account,
        BOTH,
        "读取账户：现金、总资产、持仓市值、浮动与已实现盈亏、最近一次对账差额与最近平仓结果。",
        _schema({}),
    ),
    ToolDef(
        "get_agenda",
        reads.get_agenda,
        BOTH,
        "读取当天待办：持仓复查、再次上榜的待入场计划、新的二次确认队列、剩余名额与替换候选；"
        "会话开始时先调它，按它逐项做。",
        _schema({}),
    ),
    ToolDef(
        "get_concept",
        reads.get_concept,
        BOTH,
        "读取操作手册的概念卡；不给 concept_id 时返回全部概念的目录。",
        _schema({"concept_id": _STRING}),
    ),
    ToolDef(
        "get_research",
        research_tools.get_research,
        BOTH,
        "读取研究：缺省返回全部观察中的研究；可按状态、股票或编号过滤，每份带变化记录时间线。",
        _schema({"status": _STRING, "ticker": _STRING, "research_id": {"type": "integer"}}),
    ),
    ToolDef(
        "start_research",
        research_tools.start_research,
        CHAT_ONLY,
        "用户点名发起研究（仅对话模式）：股票与论点建一份观察中的研究并记 created，"
        "有效期为配置的交易日数；该股票已有有效计划或观察中的研究时拒绝并返回那份；"
        "先复述，confirmed 为真才写，按 request_id 幂等。",
        _schema(
            {"ticker": _STRING, "thesis": _STRING, "request_id": _STRING, "confirmed": {"type": "boolean"}},
            ("ticker", "thesis", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "conclude_research",
        research_tools.conclude_research,
        BOTH,
        "保存研究结论三选一（ready 可以出计划 / watch 值得盯 / reject 否决），附理由、反证与引用；"
        "watch 带新条件是用户写入（先复述条件，confirmed 为真才写），简报模式不能改条件，有效期从当天重算。",
        _schema(
            {
                "research_id": {"type": "integer"},
                "verdict": _STRING,
                "reasons": _STRING,
                "counter": _STRING,
                "refs": _STRINGS,
                "condition": {"type": "object"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
            },
            ("research_id", "verdict", "reasons"),
        ),
    ),
    ToolDef(
        "drop_research",
        research_tools.drop_research,
        CHAT_ONLY,
        "放弃一份观察中的研究（仅对话模式，页面也转发它）；先复述，confirmed 为真才写，按 request_id 幂等。",
        _schema(
            {
                "research_id": {"type": "integer"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
                "reason": _STRING,
            },
            ("research_id", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "compute_plan_levels",
        planning.compute_plan_levels,
        BOTH,
        "按宿主算术计算某只股票的计划价位、有效期与按风险预算的股数；出计划前先调它。",
        _schema({"ticker": _STRING}, ("ticker",)),
    ),
    ToolDef(
        "save_verdict",
        planning.save_verdict,
        BOTH,
        "保存对某只股票的二次确认结论（confirm / veto / unknown），需给出理由；confirm 与 veto 要引用看过的工具调用；"
        "对有待入场计划的股票保存 veto 会把该计划作废。",
        _schema(
            {"ticker": _STRING, "verdict": _STRING, "reasons": _STRING, "counter": _STRING, "refs": _STRINGS},
            ("ticker", "verdict", "reasons"),
        ),
    ),
    ToolDef(
        "save_plan",
        planning.save_plan,
        BOTH,
        "保存某只已确认股票的交易计划；价位缺省取宿主规则价位，也可自带（须过宿主校验）；股数永远由宿主计算；"
        "该股票已有有效计划、或名额已满且不满足替换规则时拒绝。",
        _schema(
            {
                "ticker": _STRING,
                "thesis": _STRING,
                "invalidation": _STRING,
                "entry_type": _STRING,
                "max_hold_sessions": {"type": "integer"},
                "levels": {"type": "object"},
                "counter": _STRING,
                "refs": _STRINGS,
            },
            ("ticker", "thesis", "invalidation"),
        ),
    ),
    ToolDef(
        "continue_plan",
        plan_tools.continue_plan,
        BOTH,
        "延续一份再次上榜且当天重新确认的待入场计划：数字不变，有效期从今天重算，股数按当天总资产重算。",
        _schema({"plan_id": {"type": "integer"}, "refs": _STRINGS}, ("plan_id",)),
    ),
    ToolDef(
        "revise_plan",
        plan_tools.revise_plan,
        BOTH,
        "修订一份再次上榜且当天重新确认的待入场计划：改价位 / 论点 / 作废条件 / 最长持有，"
        "必须写明改了什么和原因；价位照样过宿主校验。",
        _schema(
            {
                "plan_id": {"type": "integer"},
                "reason": _STRING,
                "levels": {"type": "object"},
                "thesis": _STRING,
                "invalidation": _STRING,
                "max_hold_sessions": {"type": "integer"},
                "refs": _STRINGS,
            },
            ("plan_id", "reason"),
        ),
    ),
    ToolDef(
        "save_holding_review",
        plan_tools.save_holding_review,
        BOTH,
        "保存一只持仓的复查结论（holds / broken / unknown），附理由与引用；论点不成立时宿主会转成下次开盘卖出。",
        _schema(
            {
                "plan_id": {"type": "integer"},
                "verdict": _STRING,
                "reasons": _STRING,
                "counter": _STRING,
                "refs": _STRINGS,
            },
            ("plan_id", "verdict", "reasons"),
        ),
    ),
    ToolDef(
        "save_summary",
        plan_tools.save_summary,
        BOTH,
        "保存当天的一句话总结（1–300 字）：概括名单、计划和持仓的变化，不引入名单外的股票。",
        _schema({"text": _STRING}, ("text",)),
    ),
    ToolDef(
        "record_cash_flow",
        ledger_tools.record_cash_flow,
        CHAT_ONLY,
        "记一笔出入金：期初现金 / 入金 / 出金 / 调整（仅对话模式）；先复述，confirmed 为真才写，"
        "按 request_id 幂等；同一时间只允许一笔有效期初现金。",
        _schema(
            {
                "kind": _STRING,
                "amount": {"type": "number"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
                "effective_at": _STRING,
                "note": _STRING,
            },
            ("kind", "amount", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "confirm_stop_order",
        plan_tools.confirm_stop_order,
        CHAT_ONLY,
        "确认已在券商挂上止损单（仅对话模式）：挂单价必须等于计划当前止损；确认前今日操作一直提示未确认。",
        _schema(
            {
                "plan_id": {"type": "integer"},
                "price": {"type": "number"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
            },
            ("plan_id", "price", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "raise_stop",
        plan_tools.raise_stop,
        CHAT_ONLY,
        "把一只持仓的止损调高（仅对话模式）：只上调不下调，且须低于最新收盘；先复述，confirmed 为真才写。",
        _schema(
            {
                "plan_id": {"type": "integer"},
                "stop": {"type": "number"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
            },
            ("plan_id", "stop", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "cancel_plan",
        plan_tools.cancel_plan,
        CHAT_ONLY,
        "取消一份待入场计划（仅对话模式）；先复述，confirmed 为真才写，按 request_id 幂等。",
        _schema(
            {
                "plan_id": {"type": "integer"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
                "reason": _STRING,
            },
            ("plan_id", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "void_record",
        ledger_tools.void_record,
        CHAT_ONLY,
        "作废一笔录错的成交或出入金（仅对话模式）：作废前宿主会用剩余记录重算持仓，算不通则拒绝；"
        "先复述，confirmed 为真才写。",
        _schema(
            {
                "kind": _STRING,
                "target_id": {"type": "integer"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
                "reason": _STRING,
            },
            ("kind", "target_id", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "watchlist_add",
        watchlist_tools.watchlist_add,
        CHAT_ONLY,
        "把一只股票加进关注列表，可带论点（仅对话模式）；先复述，confirmed 为真才写。",
        _schema(
            {"ticker": _STRING, "request_id": _STRING, "confirmed": {"type": "boolean"}, "thesis": _STRING},
            ("ticker", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "watchlist_remove",
        watchlist_tools.watchlist_remove,
        CHAT_ONLY,
        "把一只股票移出关注列表（仅对话模式）；先复述，confirmed 为真才写。",
        _schema(
            {"ticker": _STRING, "request_id": _STRING, "confirmed": {"type": "boolean"}},
            ("ticker", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "record_equity",
        ledger_tools.record_equity,
        CHAT_ONLY,
        "记录一次账户对账：报券商显示的总资产，宿主返回它与自己算出的总资产的差额（仅对话模式）；"
        "先复述，confirmed 为真才写，按 request_id 幂等。",
        _schema(
            {
                "equity": {"type": "number"},
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
                "as_of": _STRING,
                "note": _STRING,
            },
            ("equity", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "record_trade",
        chat_tools.record_trade,
        CHAT_ONLY,
        "记录一笔成交（仅对话模式）：买入会关联或补录计划并返回止损单指令，卖完返回平仓结果；"
        "先复述，confirmed 为真才写，按 request_id 幂等，卖出不得超过持仓。",
        _schema(
            {
                "ticker": _STRING,
                "side": _STRING,
                "quantity": {"type": "number"},
                "price": {"type": "number"},
                "executed_at": _STRING,
                "request_id": _STRING,
                "confirmed": {"type": "boolean"},
                "fee": {"type": "number"},
                "plan_id": {"type": "integer"},
                "note": _STRING,
            },
            ("ticker", "side", "quantity", "price", "executed_at", "request_id", "confirmed"),
        ),
    ),
    ToolDef(
        "ensure_data",
        chat_tools.ensure_data,
        CHAT_ONLY,
        "给名单外的股票补日线、资讯、申报、财报日与基本面五族数据（仅对话模式），返回各族覆盖率。",
        _schema({"ticker": _STRING}, ("ticker",)),
    ),
)
_BY_NAME = {tool.name: tool for tool in _DEFS}


def tool_defs(mode: str) -> list[ToolDef]:
    return [tool for tool in _DEFS if mode in tool.modes]


def call(env: ToolEnv, name: str, args: dict) -> dict:
    """一次工具调用：信封 {'ref': 'call:N', 'data': …} 或 {'ref': 'call:N', 'error': '代码[: 说明]'}；
    携带 detail 的拒绝（如 active_plan_exists）另带 'detail' 键。"""
    if not isinstance(args, dict):
        return _finish(env, name, {}, None, "invalid_argument: arguments 必须是对象")
    defn = _BY_NAME.get(name)
    if defn is None:
        return _finish(env, name, args, None, "unknown_tool")
    if env.mode not in defn.modes:
        return _finish(env, name, args, None, "not_available_in_mode")
    try:
        _check_args(defn, args)
        data = defn.fn(env, **args)
        return _finish(env, name, args, data, None)  # 成功路径也在异常链内，序列化出错不停服务循环
    except ValueError as exc:
        return _finish(env, name, args, None, str(exc) or "invalid_argument", detail=getattr(exc, "detail", None))
    except core_tx.Unavailable:
        return _finish(env, name, args, None, "unavailable")
    except Exception as exc:  # noqa: BLE001 - 错误信封报告后继续服务下一条
        return _finish(env, name, args, None, f"invalid_argument: {str(exc)[:200]}")


def _check_args(defn: ToolDef, args: dict) -> None:
    """按 input_schema 查多余与缺失参数；save_plan 里的 shares / quantity 直接拒（仓位归宿主）。"""
    properties = defn.input_schema.get("properties", {})
    for key in args:
        if key not in properties:
            if defn.name == "save_plan" and key in ("shares", "quantity"):
                raise ValueError("shares_not_allowed: 股数由宿主计算，agent 不能指定")
            raise ValueError(f"unknown_argument: {key}")
    for key in defn.input_schema.get("required", []):
        if key not in args:
            raise ValueError(f"missing_argument: {key}")
    for key, value in args.items():
        expected = properties[key].get("type")
        if _wrong_type(value, expected):
            raise ValueError(f"invalid_argument: {key} 的类型需为 {expected}")


def _wrong_type(value: Any, expected: str | None) -> bool:
    if expected is None:
        return False
    if expected == "integer":
        return isinstance(value, bool) or not isinstance(value, int)
    if expected == "number":
        return isinstance(value, bool) or not isinstance(value, (int, float))
    if expected == "boolean":
        return not isinstance(value, bool)
    checks = {"string": str, "array": list, "object": dict}
    check = checks.get(expected)
    return not isinstance(value, check) if check is not None else False


def _finish(env: ToolEnv, name: str, args: dict, data: Any, error: str | None, detail: Any = None) -> dict:
    payload = None if error is not None else _jsonable(data)
    digest = (
        None
        if error is not None
        else hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    )
    seq = _record(env, name, args, digest, error)
    ref = f"call:{seq}" if seq is not None else RECORD_FAILURE_REF
    if error is not None:
        envelope = {"ref": ref, "error": error}
        if detail is not None:
            envelope["detail"] = _jsonable(detail)
        return envelope
    return {"ref": ref, "data": payload}


def _record(env: ToolEnv, name: str, args: dict, digest: str | None, error: str | None) -> int | None:
    """每次调用（含出错的）写一行 v3_tool_calls；写记录失败不影响返回。"""

    def _work(conn):
        seq = facts_repo.next_seq(conn, env.agent_session)
        facts_repo.insert_tool_call(
            conn,
            {
                "agent_session": env.agent_session,
                "mode": env.mode,
                "session": env.session,
                "seq": seq,
                "tool": name,
                "args": args,
                "result_sha256": digest,
                "error": error,
            },
        )
        return seq

    try:
        return env.write(_work)
    except Exception:  # noqa: BLE001 - 审计行写不进去也不挡工具结果
        return None
