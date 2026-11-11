"""v3.3 rotation orchestration over the database (spec §5, §6, §9): the exposure row, the
short-of-room gate, the comparable holdings, the comparison table the agent reads, the
withdrawal of orphaned rotation instructions and the rotation retrospect view.

Reads and the one host write (``withdraw_orphans``); the caller owns the transaction. All
arithmetic lives in ``execution.rotation`` / ``execution.plan_levels``; this module only reads
the facts and puts them in the pure functions' shapes."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from investment_assistant.config import AssistantConfig
from investment_assistant.core.texts import money_value, quantity_value
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days, trading_days_between
import investment_assistant.execution.rotation as rotation_math
from investment_assistant.derivation.indicators import price as price_indicators
from investment_assistant.derivation.indicators.screen_metrics import _ret, screen_rs
from investment_assistant.execution.plan_levels import rule_levels, size
from investment_assistant.execution.plan_line import held_sessions
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import plan_repo, rotation_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.account import account_view
from investment_assistant.facts.ranking import TIER_TEXT

HISTORY_WINDOW = 20  # 比较表里「最近几个交易日已换仓几次」的窗口（spec §6.3）
BARS_LIMIT = 400
NO_GROUP_TEXT = "未归类"
_FOUR = Decimal("0.0001")
_PERCENT = Decimal("0.01")


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value) -> str | None:
    return None if value is None else f"{money_value(value):f}"


def _ratio(value) -> str | None:
    return None if value is None else f"{_dec(value).quantize(_FOUR, rounding=ROUND_HALF_UP):f}"


def _account(conn, *, session: date, cutoff_at: datetime) -> dict:
    """账户视图的三个数：总资产快照、持仓总市值、是否有持仓缺最新收盘。"""
    view = account_view(conn, session=session, cutoff_at=cutoff_at)
    positions = view["ledger"]["positions"]
    return {
        "view": view,
        "snapshot": view["sizing_snapshot"],
        "market_value": view["ledger"]["market_value"],
        "positions": positions,
        "missing_close": any(row["market_value"] is None for row in positions),
    }


def exposure_row(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig) -> dict:
    """仓位占比、上限、剩余额度与是否有持仓缺收盘（spec §5.1）；总资产取仓位本金快照，市值取账本。"""
    account = _account(conn, session=session, cutoff_at=cutoff_at)
    snapshot = account["snapshot"]
    return rotation_math.exposure(
        equity=None if snapshot is None else _dec(snapshot["equity"]),
        market_value=account["market_value"],
        cap=Decimal(str(cfg.workbench.total_cap)),
        missing_close=account["missing_close"],
    )


def rule_sizing(
    conn,
    *,
    session: date,
    cutoff_at: datetime,
    cfg: AssistantConfig,
    ticker: str,
    sold_value: Decimal = Decimal(0),
) -> dict | None:
    """宿主规则价位与股数（spec §5.2、§6.5）：没有日线或 ATR 时 ``None``；否则价位取 ``rule_levels(当天收盘,
    ATR 14)``，股数取 ``size``，持仓总市值减去 ``sold_value``（假设已按最新收盘卖出），负股数记 0。"""
    bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
    if not bars:
        return None
    atr = price_indicators.atr14(bars)
    if atr is None or atr <= 0:
        return None
    ref_price = Decimal(str(bars[-1]["close"]))
    levels = rule_levels(ref_price, atr)
    account = _account(conn, session=session, cutoff_at=cutoff_at)
    held = next((row["market_value"] for row in account["positions"] if row["ticker"] == ticker), None)
    sizing, _ = size(
        snapshot=account["snapshot"],
        ref_price=ref_price,
        stop=levels["stop"],
        entry_high=levels["entry_high"],
        ticker_value=held if held is not None else Decimal(0),
        total_value=account["market_value"] - _dec(sold_value),
        params=cfg.workbench,
    )
    if sizing.get("shares") is not None and sizing["shares"] < 0:
        sizing = {**sizing, "shares": 0}
    return {"ref_price": ref_price, "atr": atr, "levels": levels, "sizing": sizing}


def comparable(conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, require_review: bool) -> dict:
    """可比的持仓与被排除的持仓（spec §6.2）：已买入的计划，当天止损没触及、没有卖出指令、有当天日线、
    复查结论不是「不成立」；``require_review`` 时还要当天已有复查结论。持仓行 = 计划行 + position + last_close。"""
    del cfg  # 目前无需配置；与其余读函数同签名
    by_ticker: dict[str, list[dict]] = {}
    for trade in facts_repo.list_trades(conn):
        by_ticker.setdefault(trade["ticker"], []).append(trade)
    reviews = plan_repo.holding_reviews(conn, session)
    holdings: list[dict] = []
    excluded: list[dict] = []
    for plan in sorted(plan_repo.plans_by_status(conn, ("open",)), key=lambda row: row["id"]):
        ticker = plan["ticker"]
        if ticker not in by_ticker:
            continue
        try:
            position = build(
                by_ticker[ticker], splits=data_repo.split_events(conn, ticker, cutoff_at=cutoff_at)
            )["position"]
        except PositionError:
            continue
        if position is None:
            continue
        bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at)
        unchecked = not bars or bars[-1]["price_date"] != session
        reason = rotation_math.ineligible_reason(
            plan=plan, unchecked=unchecked, review=reviews.get(plan["id"]), require_review=require_review
        )
        if reason is not None:
            excluded.append(
                {
                    "plan_id": plan["id"],
                    "ticker": ticker,
                    "reason": reason,
                    "text": rotation_math.INELIGIBLE_TEXT[reason],
                }
            )
            continue
        holdings.append({**plan, "position": position, "last_close": Decimal(str(bars[-1]["close"]))})
    return {"holdings": holdings, "excluded": excluded}


def cooldown_last_session(conn, *, session: date, cfg: AssistantConfig) -> date | None:
    """仍在冷却期内的最近一次半仓 / 全仓结论的交易日（spec §6.1 条件 5）；不在冷却期为 ``None``。"""
    cooldown = cfg.v3.rotation_cooldown_sessions
    rows = rotation_repo.effective_since(conn, add_trading_days(session, -cooldown))
    return rotation_math.cooldown_last(rows, session=session, cooldown_sessions=cooldown)


def gate(
    conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, ticker: str, mode: str
) -> dict:
    """换仓闸门（spec §6.1）：候选 B 是否「钱不够」、比不比较、不比较的原因。``short`` 为假时其余为空；
    为真时按顺序判 incomplete → no_holding → compared_today → cooldown，对话模式不挡后两种。"""
    empty = {"short": False, "compare": False, "blocked": None, "blocked_text": None, "last_session": None, "sizing": None}
    sized = rule_sizing(conn, session=session, cutoff_at=cutoff_at, cfg=cfg, ticker=ticker)
    if sized is None or not rotation_math.short_of_room(sized["sizing"], min_fill=Decimal(str(cfg.v3.rotation_min_fill))):
        return empty
    result = {**empty, "short": True, "sizing": sized}
    last = cooldown_last_session(conn, session=session, cfg=cfg)
    blocked: str | None = None
    if exposure_row(conn, session=session, cutoff_at=cutoff_at, cfg=cfg)["incomplete"]:
        blocked = "incomplete"
    elif not comparable(conn, session=session, cutoff_at=cutoff_at, cfg=cfg, require_review=False)["holdings"]:
        blocked = "no_holding"
    elif mode != "chat" and any(row["origin"] == "brief" for row in rotation_repo.rotations_on(conn, session)):
        blocked = "compared_today"
    elif mode != "chat" and last is not None:
        blocked = "cooldown"
    if blocked is None:
        return {**result, "compare": True}
    return {
        **result,
        "blocked": blocked,
        "blocked_text": rotation_math.BLOCKED_TEXT[blocked].format(last=last),
        "last_session": last if blocked == "cooldown" else None,
    }


def _stock_metrics(conn, ticker: str, *, session: date, cutoff_at: datetime, groups: dict, industry: dict) -> dict:
    """相对强度（与名单同一公式，基准 QQQ）、行业与行业档位；没有日线 / 没有基准 / 日线不够 60 天时 rs 为空。"""
    bars = data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at, limit=300)
    bench = data_repo.read_bars(conn, "QQQ", session, cutoff_at=cutoff_at, limit=300)
    rs = None
    if len(bars) > 60 and len(bench) > 60:
        rs = screen_rs(bars, _ret(bench, 20), _ret(bench, 60))
    group = groups.get(ticker)
    tier = (industry.get(group) or {}).get("tier") if group else None
    return {
        "rs": rs,
        "industry": group or NO_GROUP_TEXT,
        "industry_tier": TIER_TEXT.get(tier or "insufficient", TIER_TEXT["insufficient"]),
    }


def _day_industry(conn, session: date) -> dict:
    run = facts_repo.latest_run(conn, session)
    return ((run or {}).get("factors") or {}).get("industry") or {}


def comparison_table(
    conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, candidate: str
) -> dict:
    """摆给 agent 的比较表（spec §6.3）：候选 B、账户占比、每只可比持仓（含半仓 / 全仓各自的账与换仓后 B 能买
    多少股）、被排除的持仓、两笔交易的手续费估计与换仓频率。金额、价位、比例一律字符串。"""
    kw = {"session": session, "cutoff_at": cutoff_at, "cfg": cfg}
    account = _account(conn, session=session, cutoff_at=cutoff_at)
    snapshot = account["snapshot"]
    equity = None if snapshot is None else _dec(snapshot["equity"])
    exposure = rotation_math.exposure(
        equity=equity,
        market_value=account["market_value"],
        cap=Decimal(str(cfg.workbench.total_cap)),
        missing_close=account["missing_close"],
    )
    comp = comparable(conn, require_review=True, **kw)
    tickers = [row["ticker"] for row in comp["holdings"]] + [candidate]
    sectors = data_repo.latest_sectors(conn, tickers, cutoff_at=cutoff_at)
    groups = {ticker: (sectors.get(ticker) or {}).get("group") for ticker in tickers}
    industry = _day_industry(conn, session)

    def metrics(ticker: str) -> dict:
        return _stock_metrics(conn, ticker, session=session, cutoff_at=cutoff_at, groups=groups, industry=industry)

    base = rule_sizing(conn, ticker=candidate, **kw)
    row = facts_repo.candidate_at(conn, session, candidate)
    if row is not None:
        cand_metrics = {
            "rs": None if row["rs"] is None else _dec(row["rs"]),
            "industry": row["industry"] or NO_GROUP_TEXT,
            "industry_tier": TIER_TEXT.get(row["industry_tier"] or "insufficient", TIER_TEXT["insufficient"]),
        }
        setup_tier = TIER_TEXT.get(row["setup_tier"] or "insufficient", TIER_TEXT["insufficient"])
    else:
        cand_metrics, setup_tier = metrics(candidate), TIER_TEXT["insufficient"]
    sizing = (base or {}).get("sizing") or {}
    candidate_row = {
        "ticker": candidate,
        "rank": None if row is None else row["rank"],
        "reason": "" if row is None else row["reason"],
        "rs": _ratio(cand_metrics["rs"]),
        "industry": cand_metrics["industry"],
        "industry_tier": cand_metrics["industry_tier"],
        "setup_tier": setup_tier,
        "levels": None if base is None else {key: f"{value:f}" for key, value in base["levels"].items()},
        "risk_amount": _money(sizing.get("risk_amount")),
        "uncapped_shares": sizing.get("uncapped_shares"),
        "room_shares": None if sizing.get("shares") is None else max(0, sizing["shares"]),
    }

    def line(verdict: str, holding: dict) -> dict | None:
        done = rotation_math.sale(
            quantity=holding["position"]["quantity"],
            avg_cost=holding["position"]["avg_cost"],
            last_close=holding["last_close"],
            verdict=verdict,
        )
        if done is None:
            return None
        bought = rule_sizing(
            conn, ticker=candidate, sold_value=done["sell_quantity"] * holding["last_close"], **kw
        )
        return {
            "sell_quantity": f"{quantity_value(done['sell_quantity'])}",
            "proceeds": _money(done["proceeds"]),
            "realized_pnl": _money(done["realized_pnl"]),
            "buy_shares": None if bought is None else bought["sizing"]["shares"],
        }

    reviews = plan_repo.holding_reviews(conn, session)
    holdings = []
    for holding in comp["holdings"]:
        position, close = holding["position"], holding["last_close"]
        avg_cost = _dec(position["avg_cost"])
        market = position["quantity"] * close
        adopted = holding["kind"] == "adopted"
        initial = holding["initial_stop"]
        r_multiple = None
        if not adopted and initial is not None and avg_cost > initial:
            r_multiple = ((close - avg_cost) / (avg_cost - initial)).quantize(_PERCENT, rounding=ROUND_HALF_UP)
        held = held_sessions(holding["start_session"], session) if holding["start_session"] else 0
        stats = metrics(holding["ticker"])
        review = reviews.get(holding["id"])
        holdings.append(
            {
                "plan_id": holding["id"],
                "ticker": holding["ticker"],
                "kind": holding["kind"],
                "quantity": f"{quantity_value(position['quantity'])}",
                "avg_cost": _money(avg_cost),
                "last_close": _money(close),
                "market_value": _money(market),
                "weight_pct": None if equity is None or equity <= 0 else _ratio(market / equity),
                "unrealized_pnl": _money(position["quantity"] * (close - avg_cost)),
                "unrealized_pct": _ratio(close / avg_cost - 1),
                "r_multiple": None if r_multiple is None else f"{r_multiple:f}",
                "r_text": "补录，不计" if adopted else None,
                "stop": _money(holding["stop"]),
                "to_stop_pct": None if holding["stop"] is None else _ratio((close - holding["stop"]) / close),
                "trailing": bool(holding["trailing"]),
                "held_sessions": held,
                "sessions_left": max(0, holding["max_hold_sessions"] - held),
                "rs": _ratio(stats["rs"]),
                "industry": stats["industry"],
                "industry_tier": stats["industry_tier"],
                "review": None if review is None else {"verdict": review["verdict"], "reasons": review["reasons"]},
                "half": line("half", holding),
                "full": line("full", holding),
            }
        )
    fees = rotation_math.fee_estimate(facts_repo.list_trades(conn), lookback=cfg.v3.rotation_fee_trades)
    effective = rotation_repo.effective_since(conn, date(1970, 1, 1))
    window_start = add_trading_days(session, -(HISTORY_WINDOW - 1))
    return {
        "session": session,
        "candidate": candidate_row,
        "account": {
            "equity": _money(equity),
            "market_value": _money(account["market_value"]),
            "pct": _ratio(exposure["pct"]),
            "cap": f"{exposure['cap']:f}",
            "room": _money(exposure["room"]),
        },
        "holdings": holdings,
        "excluded": comp["excluded"],
        "fees": {
            "per_trade": _money(fees["per_trade"]),
            "total": _money(fees["total"]),
            "sample": fees["sample"],
            "note": fees["note"],
        },
        "history": {
            "last_session": max((r["session"] for r in effective if r["session"] <= session), default=None),
            "recent_count": sum(1 for r in effective if window_start <= r["session"] <= session),
            "window_sessions": HISTORY_WINDOW,
        },
    }


def withdraw_orphans(conn, *, session: date) -> list[dict]:
    """换仓指令撤回（spec §6.6）：A 带换仓指令而资金来源 B 的计划不存在或已没买就结束（过期、作废、被替换、
    取消）→ 清空 ``sell_instruction`` / ``sell_quantity`` / ``rotation_id``（一条 UPDATE，满足库约束）并记
    ``rotation_withdrawn``。返回写下的事件；同一天重跑不再产生。"""
    written: list[dict] = []
    for plan in plan_repo.plans_by_status(conn, ("open",)):
        if plan["sell_instruction"] != "rotation":
            continue
        funded = plan_repo.plan_funded_by(conn, plan["rotation_id"])
        if funded is not None and funded["status"] not in rotation_math.ENDED_TEXT:
            continue
        if funded is not None:
            cause = f"{funded['ticker']} 的计划{rotation_math.ENDED_TEXT[funded['status']]}"
        else:
            record = rotation_repo.rotation(conn, plan["rotation_id"])
            cause = f"{record['candidate'] if record else '?'} 的计划不存在"
        before = {"rotation_id": plan["rotation_id"], "sell_quantity": plan["sell_quantity"]}
        plan_repo.update_plan(conn, plan["id"], sell_instruction=None, sell_quantity=None, rotation_id=None)
        saved = plan_repo.insert_event(
            conn,
            {
                "plan_id": plan["id"],
                "session": session,
                "event": "rotation_withdrawn",
                "reason": f"换仓指令撤回：{cause}",
                "before": before,
                "after": {},
                "source": "host",
                "refs": [],
            },
        )
        if saved is not None:
            written.append(saved)
    return written


def _bars_after(conn, ticker: str, *, session: date, cutoff_at: datetime) -> list[dict]:
    return data_repo.read_bars(conn, ticker, session, cutoff_at=cutoff_at, limit=BARS_LIMIT)


def rotation_view(
    conn, *, session: date, cutoff_at: datetime, cfg: AssistantConfig, limit: int | None = None
) -> dict:
    """换仓复盘（spec §9）：每次比较一行（执行情况与 5 / 10 / 20 日对照），汇总用全部记录，
    ``limit`` 只截断 ``records``。全部由宿主从记录现算。"""
    horizons = tuple(cfg.v3.rotation_horizons)
    records: list[dict] = []
    summary_rows: list[dict] = []
    for row in rotation_repo.rotations(conn):
        record = _rotation_record(conn, row, session=session, cutoff_at=cutoff_at, horizons=horizons)
        records.append(record)
        execution = record["execution"]
        summary_rows.append(
            {
                "verdict": row["verdict"],
                "state": None if execution is None else execution["state"],
                "fee": record["fee_estimate"],
                "returns": record["compare"],
            }
        )
    return {
        "records": records if limit is None else records[:limit],
        "summary": rotation_math.rotation_summary(summary_rows, min_sample=cfg.v3.retro_min_sample),
        "min_sample": cfg.v3.retro_min_sample,
    }


def _rotation_record(conn, row: dict, *, session: date, cutoff_at: datetime, horizons: tuple[int, ...]) -> dict:
    switched = row["verdict"] in ("half", "full")
    execution = None
    if switched:
        sells = [
            trade
            for trade in facts_repo.list_trades(conn, row["holding_ticker"])
            if trade["side"] == "sell"
            and trade["voided_at"] is None
            and trade["executed_at"].astimezone(rotation_math.NY).date() > row["session"]
        ]
        events = plan_repo.events(conn, row["holding_plan_id"])
        execution = rotation_math.execution_state(
            rotation=row, events_a=events, sells_a=sells, plan_b=plan_repo.plan_funded_by(conn, row["id"])
        )
    compare = None
    if row["holding_ticker"]:
        compare = rotation_math.compare_returns(
            bars_a=_bars_after(conn, row["holding_ticker"], session=session, cutoff_at=cutoff_at),
            bars_b=_bars_after(conn, row["candidate"], session=session, cutoff_at=cutoff_at),
            session=row["session"],
            horizons=horizons,
        )
    fee = Decimal(0)
    if switched:
        fee = Decimal(str(((row["snapshot"] or {}).get("fees") or {}).get("total") or 0))
    return {
        "rotation_id": row["id"],
        "session": row["session"],
        "origin": row["origin"],
        "candidate": row["candidate"],
        "verdict": row["verdict"],
        "verdict_text": rotation_math.VERDICT_TEXT[row["verdict"]],
        "holding_ticker": row["holding_ticker"],
        "reasons": row["reasons"],
        "weakness": row["weakness"],
        "sell_quantity": row["sell_quantity"],
        "buy_shares": row["buy_shares"],
        "fee_estimate": fee,
        "execution": execution,
        "compare": compare,
    }


def week_rotations(conn, *, week_start: date, week_end: date) -> list[dict]:
    """周报用：该周每条换仓记录一行（交易日顺序）。"""
    rows = [
        row
        for day in trading_days_between(week_start, week_end)
        for row in rotation_repo.rotations_on(conn, day)
    ]
    return [
        {
            "session": row["session"],
            "candidate": row["candidate"],
            "verdict": row["verdict"],
            "verdict_text": rotation_math.VERDICT_TEXT[row["verdict"]],
            "holding_ticker": row["holding_ticker"],
        }
        for row in rows
    ]


__all__ = [
    "comparable",
    "cooldown_last_session",
    "comparison_table",
    "exposure_row",
    "gate",
    "rotation_view",
    "rule_sizing",
    "week_rotations",
    "withdraw_orphans",
]
