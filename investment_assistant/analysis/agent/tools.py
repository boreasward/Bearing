"""Agent tool layer: plain functions plus a call recorder (P3 design §5).

No LangGraph dependency here — the graph wraps these functions. The batch
injects ticker/session/cutoff per unit; tools never accept dates, so the
model cannot read anything written after the cutoff. Every call (including
errors) lands in the recorder as the ref the model may cite.
"""
from __future__ import annotations
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

from investment_assistant.execution import plan_levels as plans
from investment_assistant.execution import positions
from investment_assistant.derivation.indicators.price import indicators

NY = ZoneInfo('America/New_York')

ALLOWED_EXTRA = ('SPY', 'SMH', 'XLK', 'QQQ')
MAX_LOOKBACK = 120
PRICE_TOOLS = frozenset({'get_price_bars', 'get_indicators'})

# Keys a rule-plan row might carry; the agent must never see plan levels.
_PLAN_LEVEL_KEYS = ('entry_low', 'entry_high', 'stop', 'target1', 'ref_price', 'atr', 'sizing', 'shares',
                    'plan_version', 'valid_from', 'valid_until', 'based_on_id', 'plan_id', 'status',
                    'invalid_reasons', 'request_id', 'request_hash')


class ToolError(ValueError):
    """Bad tool arguments; recorded as an error envelope, never raised to the model."""


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def result_hash(result: dict) -> str:
    canonical = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ToolContext:
    ticker: str
    session: date
    cutoff_at: datetime
    read: Callable[[Callable[[Any], Any]], Any]   # runs fn(conn) in a read-only transaction


@dataclass
class Recorder:
    phase: str = 'propose'
    calls: list[dict] = field(default_factory=list)
    # LangGraph's ToolNode runs the tool calls of one model turn on a thread pool, so records interleave;
    # numbering and appending happen under one lock so ``seq`` is unique, gapless and monotonic
    # (``agent_tool_calls`` has a unique key on ``(run_id, seq)``).
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def record(self, tool: str, args: dict, *, data: Any = None, error: str | None = None,
               now: datetime) -> dict:
        envelope: dict = {}
        with self._lock:
            seq = len(self.calls) + 1
            envelope['ref'] = f'call:{seq}'
            if error is not None:
                envelope['error'] = error
            else:
                envelope['data'] = data
            self.calls.append({'seq': seq, 'phase': self.phase, 'tool': tool, 'args': args, 'result': envelope,
                               'result_sha256': result_hash(envelope), 'called_at': now})
        return envelope

    def refs(self, phase: str | None = None) -> set[str]:
        return {c['result']['ref'] for c in self.calls if phase is None or c['phase'] == phase}

    def price_refs(self) -> set[str]:
        return {c['result']['ref'] for c in self.calls
                if c['tool'] in PRICE_TOOLS and 'error' not in c['result']}

    def knowledge_refs(self, phase: str | None = None) -> set[str]:
        """Every ``know:<id>@<version>`` a successful ``get_knowledge`` call of this run (this phase)
        returned: the only knowledge a plan, counter, review, draft or answer may cite (spec §6.1)."""
        found: set[str] = set()
        for c in self.calls:
            if c['tool'] != 'get_knowledge' or 'error' in c['result'] or (phase is not None and c['phase'] != phase):
                continue
            data = c['result'].get('data')
            entries = data.get('entries') if isinstance(data, dict) else None
            found.update(e['ref'] for e in entries or [] if isinstance(e, dict) and isinstance(e.get('ref'), str))
        return found


def _allowed(ctx: ToolContext, ticker: Any) -> str:
    if not isinstance(ticker, str) or not ticker.strip():
        raise ToolError('ticker 必须是非空字符串')
    upper = ticker.upper().strip()
    if upper != ctx.ticker.upper() and upper not in ALLOWED_EXTRA:
        raise ToolError(f'{ticker} 不在允许的标的内（单元标的或 SPY/SMH/XLK/QQQ）')
    return upper


def _int_in(value: Any, name: str, low: int, high: int, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f'{name} 必须是整数')
    if not low <= value <= high:
        raise ToolError(f'{name} 必须在 {low}–{high} 之间')
    return value


def get_trigger(ctx: ToolContext) -> list[dict]:
    rows = ctx.read(lambda conn: data_repo.unit_candidates(conn, ctx.ticker, ctx.session)) or []
    out = []
    for row in rows:
        inputs = {k: _plain(v) for k, v in (row.get('inputs') or {}).items() if k not in _PLAN_LEVEL_KEYS}
        out.append({'rule_id': row['rule_id'], 'action': row['action'], 'inputs': inputs})
    return out


def get_price_bars(ctx: ToolContext, ticker: str, lookback: int = 60) -> list[dict]:
    upper = _allowed(ctx, ticker)
    lookback = _int_in(lookback, 'lookback', 1, MAX_LOOKBACK, 60)
    rows = ctx.read(lambda conn: data_repo.read_bars(conn, upper, ctx.session, cutoff_at=ctx.cutoff_at,
                                                      limit=lookback)) or []
    return [_plain({'date': b['price_date'], 'open': b['open'], 'high': b['high'], 'low': b['low'],
                    'close': b['close'], 'volume': b['volume']}) for b in rows]


def get_indicators(ctx: ToolContext, ticker: str) -> dict:
    upper = _allowed(ctx, ticker)

    def read_pair(conn):
        bars = data_repo.read_bars(conn, upper, ctx.session, cutoff_at=ctx.cutoff_at)
        spy = data_repo.read_bars(conn, 'SPY', ctx.session, cutoff_at=ctx.cutoff_at)
        return bars, spy

    bars, spy = ctx.read(read_pair)
    return indicators(bars or [], spy or [])


def get_filings(ctx: ToolContext, days: int = 30) -> list[dict]:
    days = _int_in(days, 'days', 1, 90, 30)
    rows = ctx.read(lambda conn: data_repo.read_filings(
        conn, ctx.ticker, since=ctx.session - timedelta(days=days), cutoff_at=ctx.cutoff_at)) or []
    return [_plain({k: r[k] for k in ('id', 'ticker', 'form', 'filing_date', 'accession_number',
                                      'primary_document', 'accepted_at', 'items')}) for r in rows]


def get_news(ctx: ToolContext, days: int = 7) -> list[dict]:
    days = _int_in(days, 'days', 1, 14, 7)
    since = datetime.combine(ctx.session - timedelta(days=days), time(0), tzinfo=NY)
    rows = ctx.read(lambda conn: data_repo.read_news(conn, ctx.ticker, cutoff_at=ctx.cutoff_at,
                                                      since=since)) or []
    return [_plain({'source': r['source'], 'title': r['title'], 'url': r['url'],
                    'published_at': r['published_at']}) for r in rows]


def get_earnings(ctx: ToolContext) -> dict | None:
    row = ctx.read(lambda conn: data_repo.latest_earnings(conn, ctx.ticker, cutoff_at=ctx.cutoff_at))
    if row is None:
        return None
    return {'earnings_date': row['earnings_date'].isoformat() if row['earnings_date'] else None,
            'status': row['status']}


def get_market_context(ctx: ToolContext) -> dict | None:
    row = ctx.read(lambda conn: data_repo.regime_for(conn, ctx.session, cutoff_at=ctx.cutoff_at))
    if row is None:
        return None
    return _plain({'label': row['label'], 'breadth': row['breadth'], 'sectors': row['sectors'],
                   'macro': row['macro']})


def get_position(ctx: ToolContext) -> dict | None:
    """The open position as the cutoff knew it (v2.9 D33): only fills recorded and executed by `cutoff_at`, and a
    void recorded after it had not happened yet."""
    def read_position(conn):
        return positions.build(execution_repo.list_trades(conn, ctx.ticker, cutoff=ctx.cutoff_at),
                               splits=data_repo.split_events(conn, ctx.ticker))['position']

    position = ctx.read(read_position)
    if position is None:
        return None
    return _plain({'quantity': position['quantity'], 'avg_cost': position['avg_cost'],
                   'opened_at': position['opened_at']})


def get_concept(ctx: ToolContext, concept_id: str | None = None) -> dict:
    """A concept card (or the index of all of them) from the versioned card directory (v2.6 spec §10).

    The one tool that ignores the cutoff: it reads no database and never touches ``ctx``; the result says
    ``point_in_time: false`` and carries the card's ref and SHA-256, and the recorder stores all of it."""
    from investment_assistant.core import knowledge

    if concept_id is not None and not isinstance(concept_id, str):
        raise ToolError('concept_id 必须是字符串')
    try:
        cat = knowledge.catalog()
    except knowledge.KnowledgeError as exc:
        raise ToolError(f'概念卡不可用：{exc}') from exc
    try:
        return knowledge.agent_text(cat, concept_id)
    except LookupError as exc:
        raise ToolError(str(exc)) from exc


KNOWLEDGE_NOTE = '这是用户审定过的经验，是判断不是事实；价位不能只引用它'


def get_knowledge(ctx: ToolContext, ticker: str | None = None, concept: str | None = None) -> dict:
    """The user's reviewed lessons ("my knowledge", v2.7 spec §6.1) as they stood at the cutoff.

    ``ticker`` (the unit's or one of the allowed extras) or ``concept`` (``plan.stop`` or
    ``concept:plan.stop@1``, the version is ignored) picks the entries attached there, neither returns
    the index (no bodies). Only versions written at or before ``ctx.cutoff_at`` come back, an item archived
    by then does not, and nothing that is still unreviewed can: the tool reads the item, version and status
    tables only. Not a price tool. Needs migration 027."""
    from investment_assistant.core import knowledge

    if ticker is not None and concept is not None:
        raise ToolError('ticker 与 concept 只能给一个')
    upper = _allowed(ctx, ticker) if ticker is not None else None
    concept_id = None
    if concept is not None:
        match = knowledge.REF_RE.fullmatch(concept.strip()) if isinstance(concept, str) else None
        if match is None:
            raise ToolError(f'未知概念 {concept}')
        concept_id = match.group(1)
        try:
            cards = knowledge.catalog().concepts
        except knowledge.KnowledgeError:
            cards = None                      # catalog unreadable: the format check above is all we can do
        if cards is not None and concept_id not in cards:
            raise ToolError(f'未知概念 {concept_id}')

    def read(conn):
        if not core_schema.growth_ready(conn):
            return None
        return data_repo.knowledge_rows(conn, cutoff=ctx.cutoff_at, ticker=upper, concept=concept_id)

    rows = ctx.read(read)
    if rows is None:
        raise ToolError('我的知识不可用：迁移 027 未执行')
    versions, statuses = rows
    seen = sorted(knowledge.visible(versions, statuses, cutoff=ctx.cutoff_at),
                  key=lambda v: (v['created_at'], v['knowledge_id']), reverse=True)
    query = {k: v for k, v in (('ticker', upper), ('concept', concept_id)) if v is not None}
    base = {'query': query, 'cutoff_at': ctx.cutoff_at.isoformat(), 'point_in_time': True}
    if not query:                               # the index: titles and attachments only, no bodies
        shown = seen[:knowledge.INDEX_MAX]
        return {**base, 'entries': [
            {'ref': knowledge.ref(v['knowledge_id'], v['version']), 'title': v['title'],
             'concepts': list(v['concepts']), 'tickers': list(v['tickers']), 'written_at': v['created_at'].isoformat()}
            for v in shown], 'omitted': len(seen) - len(shown), 'note': KNOWLEDGE_NOTE}
    shown = seen[:knowledge.KNOWLEDGE_RESULT_MAX]
    return {**base, 'entries': [
        {'ref': knowledge.ref(v['knowledge_id'], v['version']), 'id': v['knowledge_id'],
         'version': v['version'], 'title': v['title'], 'body': v['body'], 'scope': v['scope'],
         'counter_examples': v['counter_examples'], 'concepts': list(v['concepts']), 'tickers': list(v['tickers']),
         'written_at': v['created_at'].isoformat()} for v in shown],
        'omitted': len(seen) - len(shown), 'note': KNOWLEDGE_NOTE}


TOOLS: dict[str, Callable] = {
    'get_trigger': get_trigger,
    'get_price_bars': get_price_bars,
    'get_indicators': get_indicators,
    'get_filings': get_filings,
    'get_news': get_news,
    'get_earnings': get_earnings,
    'get_market_context': get_market_context,
    'get_position': get_position,
    'get_concept': get_concept,
    'get_knowledge': get_knowledge,
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    'get_trigger': '查看本单元触发了哪些规则及其输入值',
    'get_price_bars': '读取日线 K 线（date/open/high/low/close/volume），默认 60 根、最多 120 根',
    'get_indicators': '读取收盘价、ATR14、均线、20/252 日高低点、量能与相对 SPY 的强弱',
    'get_filings': '读取近若干天（默认 30，最多 90）的 SEC 文件元数据',
    'get_news': '读取近若干天（默认 7，最多 14）的资讯标题与链接',
    'get_earnings': '读取下次财报日期与状态',
    'get_market_context': '读取本交易日的市场区制标签、广度、行业强弱与利差',
    'get_position': '读取当前持仓股数与成本，无持仓则为空',
    'get_concept': '读取系统概念卡：不给 id 返回全部 id 与标题；给 id（如 `plan.stop`）返回整张卡。概念卡随代码版本走，不受截止时间约束',
    'get_knowledge': '读用户审定过的经验（我的知识）：给 ticker 或概念 id 取挂在上面的条目，不给取索引；只返回截止时间之前写入的版本；引用写 know:<id>@<版本>',
}


def call(ctx: ToolContext, recorder: Recorder, name: str, args: dict, *, now: datetime,
         tools: dict[str, Callable] | None = None) -> dict:
    """Dispatch one tool call; every failure is recorded as an error envelope, never raised.
    ``tools`` is the table to dispatch in (default ``TOOLS``; the discussion passes its wider one)."""
    try:
        tool = (TOOLS if tools is None else tools).get(name)
        if tool is None:
            raise ToolError(f'未知工具 {name}')
        data = tool(ctx, **(args or {}))
    except Exception as exc:  # noqa: BLE001 — the envelope is the only channel to the model
        return recorder.record(name, args, error=str(exc)[:300], now=now)
    return recorder.record(name, args, data=data, now=now)
