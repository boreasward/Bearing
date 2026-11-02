"""Versioned role capabilities. Fingerprints hash newline-joined sorted UTF-8 names.

Upper-layer tools delegate through immutable ToolContext bindings; a capability
name does not authorize an upward import or widen a role's toolset.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable

from investment_assistant.analysis.agent import research
from investment_assistant.analysis.agent.tools import (
    TOOL_DESCRIPTIONS, TOOLS, ToolContext, ToolError, get_indicator,
    get_research_dossier, request_data,
)
from investment_assistant.data.registry import tool_families

_LEGACY = frozenset(TOOLS)
_PROPOSE = _LEGACY | {'get_indicator', 'request_data'}
_ROADMAP = frozenset({'get_roadmap', 'get_concept', 'get_knowledge'})
TOOLSETS: dict[str, frozenset[str]] = {
    'propose@agent-4': _PROPOSE,
    'counter@agent-4': _PROPOSE,
    'research@agent-4': frozenset({'get_fundamentals', 'get_guidance', 'get_filings', 'get_filing_facts',
        'get_filing_text', 'get_sec_hits', 'get_earnings', 'get_news', 'get_concept', 'get_knowledge',
        'request_data', 'get_research_dossier'}),
    'review@agent-review-2': _LEGACY,
    'portfolio@agent-portfolio-2': frozenset({'get_price_bars', 'get_indicators', 'get_market_context',
        'get_concept', 'get_knowledge', 'get_indicator'}),
    'pm@agent-pm-1': frozenset({'get_concept', 'get_knowledge', 'get_indicator', 'get_market_context'}),
    'optimize@agent-optimize-1': _ROADMAP,
    'brief@agent-impl-brief-1': _ROADMAP,
    'retro@agent-retro-2': _LEGACY,
    'discuss@agent-discuss-1': _LEGACY | {'get_plan_context', 'get_derivation', 'get_agent_run', 'get_roadmap'},
}

FINGERPRINTS = {
    'propose@agent-4': 'c80a16a979906f4623f8dad2dc73975ce592556dc063f781e597d45955f53cac',
    'counter@agent-4': 'c80a16a979906f4623f8dad2dc73975ce592556dc063f781e597d45955f53cac',
    'research@agent-4': '2998db72096759d5843010a276e8d7587b2aa5e8d02729b5543a620aab9ee2aa',
    'review@agent-review-2': 'fe0018df8822748bc02b5557e95890ba7826e141310d59e58d1183d1431258cd',
    'portfolio@agent-portfolio-2': 'f27d7033b6fbf45f15c0eff37636212f706969a0544a4f9b4be472d280c5a468',
    'pm@agent-pm-1': '0bc3fe49cd86915b5fea1713977f22f74402efc11722b32dbfbdf89cf20eafeb',
    'optimize@agent-optimize-1': 'c0aecf31c5b8cad094bdd76515417210a91c406486a6c603298fb3e4ee513fdd',
    'brief@agent-impl-brief-1': 'c0aecf31c5b8cad094bdd76515417210a91c406486a6c603298fb3e4ee513fdd',
    'retro@agent-retro-2': 'fe0018df8822748bc02b5557e95890ba7826e141310d59e58d1183d1431258cd',
    'discuss@agent-discuss-1': 'b32360fc4ff784ec0129a7a1dc34dafb4f7e14cec0129d117c7b77351f1de723',
}


def _binding(ctx, name):
    fn = ctx.bindings.get(name)
    if fn is None:
        raise ToolError(f'tool_binding_missing: {name}')
    return fn


def get_roadmap(ctx: ToolContext, section: str, query: str | None = None) -> dict:
    return _binding(ctx, 'get_roadmap')(ctx, section, query=query)


def get_plan_context(ctx: ToolContext) -> dict:
    return _binding(ctx, 'get_plan_context')(ctx)


def get_derivation(ctx: ToolContext, plan_id: str) -> dict:
    return _binding(ctx, 'get_derivation')(ctx, plan_id)


def get_agent_run(ctx: ToolContext, run_id: str) -> dict:
    return _binding(ctx, 'get_agent_run')(ctx, run_id)


HOST_TOOLS = frozenset({'get_roadmap', 'get_plan_context', 'get_derivation', 'get_agent_run'})
_FUNCTIONS = TOOLS | {
    'request_data': request_data, 'get_indicator': get_indicator, 'get_research_dossier': get_research_dossier,
    'get_fundamentals': research.get_fundamentals, 'get_guidance': research.get_guidance,
    'get_filing_facts': research.get_filing_facts, 'get_filing_text': research.get_filing_text,
    'get_sec_hits': research.get_sec_hits, 'get_roadmap': get_roadmap,
    'get_plan_context': get_plan_context, 'get_derivation': get_derivation, 'get_agent_run': get_agent_run,
}
DESCRIPTIONS = TOOL_DESCRIPTIONS | {
    'request_data': '登记补查，本轮不可见；可选数据族：' + '；'.join(f'{a.id}: {a.tool}' for a in tool_families()),
    'get_indicator': '按截止时间读取单个注册指标，含口径、窗口与版本；breadth使用宿主冻结样本',
    'get_research_dossier': '读取截止时间之前的指定标的研究档案结论（不是交易指令）',
    'get_fundamentals': '读取最近八个财季的基本面、口径、原始申报来源与同比',
    'get_guidance': '读取手工录入的指引与同口径实际值比较',
    'get_filing_facts': '读取本标的某份已入库申报的原文事实、指引、进展、风险与警告',
    'get_filing_text': '读取本地原文节选（最多6000字）、SHA256及字节范围；节选不明则报错',
    'get_sec_hits': '读取最近至多90天的SEC 8-K命中与已入库正文事实',
    'get_roadmap': '读取截止时间之前的路线记录；section为goals/versions/decisions/proposals',
    'get_plan_context': '读取本次讨论的计划或复盘卡上下文',
    'get_derivation': '读取本次讨论单元内计划的独立推导',
    'get_agent_run': '读取本次讨论单元内Agent运行的推理步骤，不含讨论或复盘草稿',
}


def tools_for(key: str) -> dict[str, Callable]:
    return {name: _FUNCTIONS[name] for name in sorted(TOOLSETS[key])}


def fingerprint(key: str) -> str:
    return hashlib.sha256('\n'.join(sorted(TOOLSETS[key])).encode('utf-8')).hexdigest()


def require_bindings(ctx: ToolContext, key: str) -> None:
    for name in sorted(TOOLSETS[key] & HOST_TOOLS):
        _binding(ctx, name)
