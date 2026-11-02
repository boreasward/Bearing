"""The portfolio manager (``agent-pm-1``, v2.14 spec §7.2): commentary on the real account's hard portfolio check.

One model run per session on the real account. The host has already computed the hard result (``execution.portfolio``:
who can be done together, who is blocked, the greedy cash order); the model only ranks the ``ok`` plans, writes the
trade-offs behind the order and a suggestion per holding. It can neither move a level or a share count nor change a
verdict — ``pm_check.check_pm`` rejects the whole output when it tries. A failure leaves the hard result alone."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from langchain_core.language_models import BaseChatModel

from investment_assistant.analysis.agent import graph, pm_check, toolsets
from investment_assistant.analysis.agent.research_check import Rejected as PmRejected
from investment_assistant.analysis.agent.tools import Recorder, ToolContext, ToolError
from investment_assistant.core.versions import PM_AGENT_VERSION as PM_AGENT_VERSION
from investment_assistant.execution import portfolio
from investment_assistant.execution.citations import Rejected
from investment_assistant.execution.portfolio_types import PortfolioResult

PM_TOOLS = ('get_concept', 'get_knowledge', 'get_indicator', 'get_market_context')
PM_PLAN_KEYS = ('plan_id', 'ticker', 'chain_signal_date', 'entry_low', 'entry_high', 'stop', 'target1')
PM_POSITION_KEYS = ('ticker', 'quantity', 'market_value', 'last_close', 'r_now', 'stop', 'instruction')

PM_PROMPT = """你是组合经理，只对真实账户当天的组合给出取舍说明，不下单、不改计划。
证据里的 [fact:portfolio_check] 是程序算出的硬结果：每条入场计划的 verdict（ok 可执行、blocked 被拦并带原因、deferred 因现金暂缓）、按信号日先后装入现金的顺序 order、行业敞口 exposure_by_sector 和同行业互斥组 conflicts。这些你不能改，也不要质疑它们的算法。
你要做三件事：
1. ranking：只在 verdict 为 ok 的计划里排优先级，可以只排一部分，rank 从 1 起不重复，每条一句理由；
2. tradeoffs：说明取舍，例如先做哪条、行业或现金约束最该留意什么；每条取舍的 refs 必须是 "fact:portfolio_check" 或你这次调用工具得到的 "call:N"；
3. hold_actions：对 [fact:positions] 里的每只持仓给出 keep、trim 或 exit 的建议和理由。
不得出现任何价位、股数、数量或订单字段；不得排序 blocked 或 deferred 的计划；不要考虑证据之外的标的。
只能使用 get_concept、get_knowledge、get_indicator、get_market_context；get_indicator 的标的限计划与持仓。
完成后只输出一个 JSON 对象，不要输出其他文字：
{"summary": "…（1 到 300 字）",
 "ranking": [{"plan_id": "…", "rank": 1, "reason": "…"}],
 "tradeoffs": [{"text": "…", "refs": ["fact:portfolio_check"]}],
 "hold_actions": [{"ticker": "NVDA", "suggestion": "keep" 或 "trim" 或 "exit", "reason": "…"}]}"""


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _section(key: str, value) -> str:
    return f'[fact:{key}]\n' + json.dumps(_plain(value), ensure_ascii=False)


def _pick(rows, keys) -> list[dict]:
    return [{key: row.get(key) for key in keys if key in row} for row in rows or []]


def evidence(result: PortfolioResult, facts: dict | None) -> str:
    """The PM's evidence: the hard result as the account's own frozen record, plus the plans' levels and the
    holdings the commentary talks about. Account name and credentials never enter."""
    facts = facts or {}
    account = facts.get('account') or {}
    return '\n\n'.join([
        _section('account', {key: account[key] for key in ('cash', 'equity', 'params') if key in account}),
        _section('portfolio_check', {'status': 'ready', **portfolio.to_fact(result)}),
        _section('plans', _pick(facts.get('plans'), PM_PLAN_KEYS)),
        _section('positions', _pick(facts.get('positions'), PM_POSITION_KEYS)),
    ])


def run_pm(result: PortfolioResult, *, account: dict, ctx: ToolContext, model: BaseChatModel, checkpointer,
           run_id: str, now: Callable[[], datetime], facts: dict | None = None) -> dict:
    """One PM run: ``{status, reason, output, calls, input_tokens, output_tokens}`` like every single-phase run.

    ``output`` is the validated commentary ``{summary, ranking, tradeoffs, hold_actions}`` when ``status`` is ``ok``;
    a rejection carries the ``pm_*`` reason. ``facts`` (optional) brings the plans' levels and the holdings."""
    facts = facts or {}
    ok_plans = frozenset(str(v['plan_id']) for v in result.verdicts if v['verdict'] == 'ok')
    allowed = ({str(v['ticker']).upper() for v in result.verdicts}
               | {str(p['ticker']).upper() for p in facts.get('positions') or []})

    def scoped(context: ToolContext, ticker):
        if not isinstance(ticker, str) or ticker.strip().upper() not in allowed:
            raise ToolError(f'{ticker} 不在本次组合检查的计划与持仓标的内')
        return replace(context, ticker=ticker.strip().upper())

    def get_indicator(context: ToolContext, ticker: str, indicator_id: str):
        scoped_ctx = scoped(context, ticker)
        return toolsets.tools_for('pm@agent-pm-1')['get_indicator'](scoped_ctx, scoped_ctx.ticker, indicator_id)

    available = toolsets.tools_for('pm@agent-pm-1')
    tools = {name: available[name] for name in PM_TOOLS}
    tools['get_indicator'] = get_indicator
    recorder = Recorder(phase='pm')

    def check(text: str) -> dict:
        refs = frozenset(f"call:{call['seq']}" for call in recorder.calls)
        try:
            return pm_check.check_pm(text, ok_plan_ids=ok_plans, refs=refs)
        except PmRejected as exc:       # the single-phase graph rejects with the citations exception
            raise Rejected(exc.reason) from None

    return graph._single_run(
        phase='pm', ctx=ctx, model=model, checkpointer=checkpointer, run_id=run_id, now=now, tools=tools,
        recorder=recorder, check=check, system=PM_PROMPT,
        human=f"真实账户 · 决策交易日 {ctx.session} · 数据截至 {ctx.cutoff_at.isoformat()}\n\n{evidence(result, facts)}")
