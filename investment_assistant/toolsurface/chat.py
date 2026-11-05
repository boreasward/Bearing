"""Chat-only tools: confirmed writes of equity and trades, manual family backfill (spec §8)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from investment_assistant.data import repo as data_repo
from investment_assistant.data.identity import stock_id
from investment_assistant.execution.positions import PositionError, build
from investment_assistant.facts import collect as facts_collect
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts.coverage import coverage, coverage_lines
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import normalize_ticker

ENSURE_FAMILIES = ('bars', 'news', 'sec_filings', 'earnings', 'fundamentals')
SIDE_TEXT = {'buy': '买入', 'sell': '卖出'}


def _number(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    """JSON 数值转 Decimal；bool 不是数，零只在允许时有效（否则 invalid_argument）。"""
    number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if number is None:
        raise ValueError(f'invalid_argument: {name} 必须是数字')
    try:
        converted = Decimal(str(number))
    except InvalidOperation:
        raise ValueError(f'invalid_argument: {name} 必须是数字') from None
    if converted < 0 or (converted == 0 and not allow_zero):
        raise ValueError(f'invalid_argument: {name} 必须大于零')
    return converted


def _text(value: Any, name: str) -> str:
    text = value if isinstance(value, str) else None
    if text is None or not text:
        raise ValueError(f'invalid_argument: {name} 必须是非空字符串')
    return text


def _confirmed_flag(value: Any) -> bool:
    """非布尔输入按 invalid_argument 错误信封拒绝（工具面错误码约定用 ValueError）。"""
    flag = value if isinstance(value, bool) else None
    if flag is None:
        raise ValueError('invalid_argument: confirmed 必须是布尔值')
    return flag


def _note_text(value: Any) -> str:
    text = value if isinstance(value, str) else None
    if text is None:
        raise ValueError('invalid_argument: note 必须是字符串')
    return text


def _as_of_date(value: Any) -> date:
    text = value if isinstance(value, str) else ''
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError('invalid_argument: as_of 必须是 YYYY-MM-DD') from None


def _executed_at(value: Any) -> datetime:
    text = value if isinstance(value, str) else ''
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError('invalid_argument: executed_at 必须是 ISO 时间') from None
    if parsed.tzinfo is None:
        raise ValueError('invalid_argument: executed_at 需带时区')
    return parsed


def record_equity(env: ToolEnv, *, equity, request_id, confirmed, as_of=None, note: str = '') -> dict:
    """confirmed 为假只复述不写；为真幂等写入（同 request_id 重放仍一行）。"""
    amount = _number(equity, 'equity')
    request_id = _text(request_id, 'request_id')
    is_confirmed = _confirmed_flag(confirmed)
    note_text = _note_text(note)
    as_of_date = env.session if as_of is None else _as_of_date(as_of)
    preview = f'将记录账户本金 {amount} 元（截至 {as_of_date.isoformat()}）。确认后写入。'
    if not is_confirmed:
        return {'write': False, 'preview': preview}
    return env.write(lambda conn: facts_repo.insert_equity(
        conn, {'request_id': request_id, 'as_of': as_of_date, 'equity': amount, 'note': note_text}))


def record_trade(env: ToolEnv, *, ticker: str, side: str, quantity, price, executed_at: str,
                 request_id: str, confirmed, fee=0, plan_id=None, note: str = '') -> dict:
    ticker = normalize_ticker(ticker)
    side_text = side if isinstance(side, str) and side in SIDE_TEXT else ''
    if not side_text:
        raise ValueError('invalid_argument: side 必须是 buy 或 sell')
    qty = _number(quantity, 'quantity')
    deal_price = _number(price, 'price')
    deal_fee = _number(fee, 'fee', allow_zero=True)
    request_id = _text(request_id, 'request_id')
    is_confirmed = _confirmed_flag(confirmed)
    note_text = _note_text(note)
    executed = _executed_at(executed_at)
    linked_plan = plan_id if isinstance(plan_id, int) and not isinstance(plan_id, bool) else None
    if plan_id is not None and linked_plan is None:
        raise ValueError('invalid_argument: plan_id 必须是整数')
    preview = (f'将记录成交：{ticker} {SIDE_TEXT[side_text]} {qty} 股，价格 {deal_price}，'
               f'时间 {executed.isoformat()}。确认后写入。')
    if not is_confirmed:
        return {'write': False, 'preview': preview}

    def _work(conn):
        if linked_plan is not None:
            found = conn.execute('SELECT id FROM v3_plans WHERE id = %s', (linked_plan,)).fetchone()
            if found is None:
                raise ValueError(f'invalid_argument: plan_id {linked_plan} 不存在')
        row = {'id': None, 'request_id': request_id, 'ticker': ticker, 'side': side_text,
               'quantity': qty, 'price': deal_price, 'fee': deal_fee, 'executed_at': executed,
               'plan_id': linked_plan, 'note': note_text}
        # 先把这笔并入现有成交试算 FIFO；卖出超过持仓则拒绝且不写入
        trades = [*facts_repo.list_trades(conn, ticker), row]
        try:
            build(trades, splits=data_repo.split_events(conn, ticker, cutoff_at=env.cutoff_at))
        except PositionError as exc:
            raise ValueError(f'position_invalid: {exc}') from None
        return facts_repo.insert_trade(conn, row)
    return env.write(_work)


def ensure_data(env: ToolEnv, *, ticker: str) -> dict:
    """给名单外的股票补五个族的数据（chat 专用，spec §8）；返回按族整理的覆盖率。"""
    ticker = normalize_ticker(ticker)
    try:
        stock_id(ticker)
    except ValueError:
        raise ValueError('invalid_ticker: 非美股代码') from None
    now = datetime.now(UTC)
    rows = facts_collect.collect_extra([ticker], session=env.session, now=now,
                                       run_id=facts_collect.new_run_id(now), trigger='manual_family',
                                       cfg=env.cfg, families=ENSURE_FAMILIES)
    cov = coverage(rows)
    return {'families': cov['families'], 'lines': coverage_lines(cov)}
