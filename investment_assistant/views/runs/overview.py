"""Run overview: one sentence per batch stage, built only from the ``stage_runs`` rows (v2.6 spec §7).

``describe`` / ``build`` are pure: the numbers (``input_count``, ``output_count``, ``skips``, ``failures``,
``degradations``, ``reason``, ``duration_ms``) are the stored row's own values, only text fields (``*_text``,
``sentence``, ``headline``) and the stage ``concepts`` are added. ``overview`` is the read-only use case (a
read-only transaction, no external data source) and ``main`` the command line. Nothing here reads a table
other than ``stage_runs`` (D9).
"""
from __future__ import annotations
import investment_assistant.core.schema as core_schema
import investment_assistant.workflow.repo as workflow_repo

import argparse
import sys
from datetime import UTC, date, datetime
from typing import Any

from investment_assistant.core.constants import SH as SH
from investment_assistant.core.texts import AGENT_REASON_TEXT as AGENT_REASON_TEXT, RUN_REASON_TEXT as REASON_TEXT
from investment_assistant.core import texts as labels, stages as stages_module, tx as service
from investment_assistant.core import tx as core_tx

STATUS_TEXT: dict[str, str] = {
    'success': '成功', 'partial': '部分失败', 'degraded': '降级', 'failed': '失败', 'skipped': '跳过',
    'missing': '缺证据', 'not_recorded': '未记录',
}

# Reason / degradation-kind texts that agent.REASON_TEXT does not have (spec §7.4, D28); a code found in
# neither table is shown as is. The last block answers the per-rule skip reasons `<rule_id>:<reason>`:
# every value `rules.precondition` returns, plus `earnings_time_unknown`. The table lives in
# core/texts.py (RUN_REASON_TEXT); imported above as REASON_TEXT until Task 1.

TRIGGER_TEXT: dict[str, str] = {'scheduled': '定时', 'manual_batch': '手动'}
CLOSED_TEXT = '休市'

# Concept cards each stage line links to (spec §7.3, right column).
STAGE_CONCEPTS: dict[str, tuple[str, ...]] = {
    'sec_monitor': ('family.sec_filings', 'system.daily_workflow'),
    'collect': ('family.bars', 'family.news', 'family.sec_filings', 'family.fred'),
    'screen': ('system.screen', 'family.universe'),
    'regime': ('indicator.breadth', 'indicator.regime', 'indicator.sector_strength'),
    'signals': ('system.decision_unit', 'system.conflict'),
    'plan': ('plan.entry_zone', 'plan.stop', 'plan.target1', 'plan.status', 'plan.derivation'),
    'agent': ('agent.action_levels', 'agent.thesis', 'agent.counter', 'agent.reject_reasons'),
    'review': ('agent.review_outcome', 'agent.signals', 'review.chain_state'),
    'accounts': ('account.ledger', 'account.paper', 'account.fill_rules', 'account.follow_plans',
                 'account.agent_portfolio'),
    'retro': ('review.retro_card', 'review.retro_end'),
    'push': ('system.push',),
}

NOT_READY_NOTE = '迁移 025 未执行，暂无阶段证据'
NO_ROWS_NOTE = '这一天没有阶段证据：当天没有运行，或运行早于 v2.4（2026-10-27 起才记录）'
STAGES_1_NOTE = '这次运行按 stages-1 记账（早于 v2.5 或迁移 026 未执行），没有每日复查阶段'
STAGES_1_RETRO_NOTE = '这次运行按 stages-1 记账（早于 v2.5 或迁移 026 未执行），没有每日复查和复盘卡阶段'
STAGES_2_NOTE = '这次运行按 stages-2 记账（早于 v2.7 或迁移 027 未执行），没有复盘卡阶段'
MISSING_NOTE = '这一阶段没有证据行（记账失败，见运行报告）'
UNFINISHED_NOTE = '还没有这一阶段的记录：运行可能仍在进行，或中途被终止'
RUNNING_SUFFIX = '（可能仍在运行）'
UNKNOWN_STAGE_NOTE = '未知阶段'
CLOSED_HEADLINE = '纽约休市，未运行（只推送一行休市说明）'
SKIP_SAMPLE_REASONS = 3                 # the sentence names the first three skip reasons (spec §7.3)


# ---------------------------------------------------------------- reason texts

def reason_text(code: str | None) -> str | None:
    """The Chinese text of a reason code, a degradation kind or a parameterised reason; unknown codes as is.

    ``agent_unavailable: <x>`` -> "Agent 不可用：<x>" (x itself translated when it is a known code),
    ``aborted: <x>`` -> "运行中断：<x>", ``<rule_id>:<reason>`` -> "<规则中文名>：<原因文案>"."""
    if code is None or code == '':
        return None
    if code in REASON_TEXT:
        return REASON_TEXT[code]
    if code in AGENT_REASON_TEXT:
        return AGENT_REASON_TEXT[code]
    head, sep, rest = code.partition(':')
    rest = rest.strip()
    if sep and rest:
        if head == 'agent_unavailable':
            return f'Agent 不可用：{reason_text(rest)}'
        if head == 'aborted':
            return f'运行中断：{rest}'
        if head in labels.RULE_TEXT:
            return f'{labels.RULE_TEXT[head]}：{reason_text(rest)}'
    return code


# ---------------------------------------------------------------- one stage line

def _get(source: Any, *path: str) -> Any:
    """``source[path[0]][path[1]]…`` or None when any step is missing (an old row's detail lacks keys)."""
    for key in path:
        if not isinstance(source, dict) or key not in source:
            return None
        source = source[key]
    return source


def _join(parts: list[str], sep: str = ' · ') -> str:
    return sep.join(part for part in parts if part)


def _pair(label: str, value: Any) -> str:
    return f'{label} {value}' if value is not None else ''


def _collect(r: dict) -> str:
    text = f"采集 {r['output_count']}/{r['input_count']} 个目标（日线、资讯、SEC、FRED 按族计）"
    ok, targets = _get(r['detail'], 'earnings', 'ok'), _get(r['detail'], 'earnings', 'targets')
    if ok is not None and targets is not None:
        text += f'；财报日 {ok}/{targets}'
    return text


def _screen(r: dict) -> str:
    return f"纳指 100 范围 {r['input_count']} 只，今日入选 {r['output_count']} 只进扩展池"


def _regime(r: dict) -> str:
    return f"用 {r['input_count']} 只股票的广度生成区制快照 {r['output_count']} 份"


def _signals(r: dict) -> str:
    text = f"评估 {r['input_count']} 只，产生 {r['output_count']} 条候选"
    units = _get(r['detail'], 'units')
    if isinstance(units, dict):
        shown = _join([_pair(label, units[key]) for key, label in (('buy', '买入'), ('sell', '卖出'), ('watch', '观察'))
                       if key in units])
        if shown:
            text += f'；决策单元：{shown}'
    return text


def _plan(r: dict) -> str:
    text = f"{r['input_count']} 个买入单元，生成规则计划 {r['output_count']} 份"
    status = _join([_pair('有效', _get(r['detail'], 'status', 'ok')), _pair('作废', _get(r['detail'], 'status', 'invalid'))])
    if status:
        text += f'（{status}）'
    merged = _get(r['detail'], 'retriggers', 'count')
    if merged:                                   # v2.9 §4.2: units that merged into an active chain
        by_state = _get(r['detail'], 'retriggers', 'by_state') or {}
        text += f"；{merged} 个单元并入已有活跃链（" + _join([_pair('入场期', by_state.get('entry', 0)),
                                                           _pair('持仓', by_state.get('holding', 0)),
                                                           _pair('退出期', by_state.get('exiting', 0))]) + '）'
    checked, consistent = _get(r['detail'], 'derivation', 'checked'), _get(r['detail'], 'derivation', 'consistent')
    if checked is not None and consistent is not None:
        text += f'；推导核对一致 {consistent}/{checked}'
    return text


def _plan_ids(r: dict) -> int:
    ids = _get(r['outputs'], 'plan_ids')
    return len(ids) if isinstance(ids, list) else 0


def _agent(r: dict) -> str:
    text = (f"Agent 处理 {r['input_count']} 个单元，{r['output_count']} 个给出结果，"
            f"新 Agent 计划 {_plan_ids(r)} 份")
    tokens_in, tokens_out = _get(r['detail'], 'tokens', 'input'), _get(r['detail'], 'tokens', 'output')
    shown = _join([_pair('token 输入', tokens_in), _pair('输出', tokens_out)])
    if shown:
        text += f'；{shown}'
    merged = _get(r['detail'], 'merged')
    if merged:                                   # v2.9 §4.3: not proposed again, the chain already exists
        text += f'；{merged} 个单元并入已有链，不重复提议'
    return text


def _review(r: dict) -> str:
    text = f"复查 {r['input_count']} 份 Agent 计划"
    outcomes = _get(r['detail'], 'outcomes')
    if isinstance(outcomes, dict):
        parts = []
        if 'maintain' in outcomes:
            parts.append(f"维持 {outcomes['maintain']}")
        if 'revise' in outcomes:
            parts.append(f"修订 {outcomes['revise']}（出新版 {_plan_ids(r)}）")
        if 'invalidate' in outcomes:
            parts.append(f"作废 {outcomes['invalidate']}")
        if parts:
            text += '：' + _join(parts).replace('） · ', '）· ')     # spec §7.3: no space after the closing bracket
    decided = _join([_pair('系统规则判定', _get(r['detail'], 'decided_by', 'host')),
                     _pair('Agent 判定', _get(r['detail'], 'decided_by', 'agent'))])
    if decided:
        text += f'；{decided}'
    ended = _get(r['detail'], 'ended')
    if isinstance(ended, dict) and 'superseded' in ended:         # timeline-2 rows (v2.9 §6.3)
        total = sum(int(ended.get(k) or 0) for k in ('expired', 'invalidated', 'position_closed', 'superseded',
                                                     'closed_by_decision'))
        if total:
            parts = [_pair('过期', ended.get('expired', 0)), _pair('作废', ended.get('invalidated', 0)),
                     _pair('平仓', ended.get('position_closed', 0)), _pair('被取代', ended.get('superseded', 0))]
            if ended.get('closed_by_decision'):
                parts.append(_pair('否决', ended['closed_by_decision']))
            text += f'；已结束 {total}（' + _join(parts) + '）'
    elif isinstance(ended, dict) and any(ended.get(k) for k in ('expired', 'closed_by_decision', 'position_closed')):
        text += '；已结束的链：' + _join([_pair('过期', ended.get('expired', 0)),
                                         _pair('否决', ended.get('closed_by_decision', 0)),
                                         _pair('平仓', ended.get('position_closed', 0))])
    retriggered = _get(r['detail'], 'signals', 'retrigger')
    if retriggered:
        text += f'；再次触发 {retriggered} 条'
    exiting = _get(r['detail'], 'exiting')
    if isinstance(exiting, list) and exiting:
        text += f'；退出期不复查 {len(exiting)} 条'
    holding_rows = _get(r['detail'], 'holding_status', 'rows')
    if holding_rows is not None:
        exits = _get(r['detail'], 'holding_status', 'by_instruction', 'exit') or 0
        text += f'；持仓状态 {holding_rows} 行（退出指令 {exits}）'
    without = _get(r['detail'], 'holdings_without_chain')
    if isinstance(without, list) and without:
        text += '；无 Agent 计划的持仓：' + ', '.join(str(t) for t in without)
    return text


RETRO_KIND_TEXT = (('position_closed', '平仓'), ('stop', '止损'), ('target', '到 T1'), ('expired', '过期'),
                   ('invalidated', '作废'))


def _retro(r: dict) -> str:
    """v2.7 §9 的两句模板；数字都是该行自己的值（detail 缺键按 0）。"""
    detail = r['detail'] if isinstance(r['detail'], dict) else {}
    half = ''
    if 'weekly' in detail or 'monthly' in detail:        # v2.8: the weekly / monthly review part of the node
        half = f"周度复盘：{_tick_text(detail.get('weekly'))}；月度：{_tick_text(detail.get('monthly'))}"
        if detail.get('cards') is False:                 # a non-trading day: no card part ran
            return half
        half = '；' + half
    return _retro_cards(r, detail) + half


def _retro_cards(r: dict, detail: dict) -> str:
    events, new_cards = int(detail.get('events') or 0), int(detail.get('new_cards') or 0)
    to_review = int(detail.get('to_review') or 0)
    if not events and not new_cards and not r['input_count']:
        return f'没有新结束的计划或平仓；待审定 {to_review} 张'
    kinds = detail.get('by_end_kind') if isinstance(detail.get('by_end_kind'), dict) else {}
    parts = [f'{text} {kinds[key]}' for key, text in RETRO_KIND_TEXT if kinds.get(key)]
    shown = f'（{" · ".join(parts)}）' if parts else ''
    counts = detail.get('drafts') if isinstance(detail.get('drafts'), dict) else None
    attempted = (sum(int(counts.get(k) or 0) for k in ('ok', 'rejected', 'error')) if counts is not None
                 else r['input_count'])                              # spec §9: {ok}/{attempted}; skipped are not attempts
    return (f"新结束 {events} 笔{shown}，新建复盘卡 {new_cards} 张；"
            f"起草 {r['output_count']}/{attempted} 张；待审定 {to_review} 张")


def _sec_monitor(r: dict) -> str:
    """v2.8 §7.4: the monitor's scan / archive / notification counts, or the downloader's; the failed tickers follow."""
    if _get(r['detail'], 'mode') == 'downloader':
        text = f"下载器模式：新下载 {r['output_count']} 份申报"
    else:
        sent = _get(r['detail'], 'notifications', 'sent') or 0
        text = f"扫描 {r['input_count']} 只，新归档 {r['output_count']} 份申报，通知 {sent} 条"
    if r['failures']:
        text += f"；{len(r['failures'])} 只失败"
    return text


def _tick_text(tick: Any) -> str:
    """One review part (weekly / monthly) of the retro node: pushed, generated but not pushed, or not due."""
    if not isinstance(tick, dict):
        return '未到期'
    if tick.get('action') == 'error':
        return '出错'
    if tick.get('action') in ('generated', 'reused', 'reminded'):
        push = tick.get('push')
        return '已推送' if isinstance(push, dict) and push.get('sent') else '已生成未推送'
    return '未到期'


def _push(r: dict) -> str:
    messages = _get(r['outputs'], 'push', 'messages')
    messages = r['output_count'] if messages is None else messages
    if _get(r['detail'], 'closed'):
        return f'纽约休市，发送休市说明 {messages} 条'
    if r['input_count'] == 0 and r['output_count'] == 0 and r['status'] == 'success':
        return '没有可发的消息'
    return f'发送 {messages} 条消息'


def _accounts(r: dict) -> str:
    return f"处理 {r['input_count']} 个账户，完成 {r['output_count']} 个"


TEMPLATES = {'sec_monitor': _sec_monitor, 'collect': _collect, 'screen': _screen, 'regime': _regime, 'signals': _signals, 'plan': _plan,
             'agent': _agent, 'review': _review, 'accounts': _accounts, 'retro': _retro, 'push': _push}


def _suffixes(skips: list[dict], failures: list[dict], degradations: list[dict]) -> str:
    """The fixed order of spec §7.3: failures, degradations, skips (each omitted when empty)."""
    text = ''
    if failures:
        text += f'；{len(failures)} 处失败'
    if degradations:
        text += '；降级：' + '、'.join(str(reason_text(d.get('kind')) or d.get('kind') or '未知')
                                      for d in degradations)
    if skips:
        total = sum(int(s.get('count') or 0) for s in skips)
        named = '、'.join(f"{reason_text(s.get('reason')) or s.get('reason') or '未知'} × {s.get('count')}"
                         for s in skips[:SKIP_SAMPLE_REASONS])
        text += f'；跳过 {total}（{named}）'
    return text


def _sentence(r: dict) -> str:
    status, reason = r['status'], r.get('reason')
    if status == 'skipped':
        if r['stage'] == 'retro' and reason == 'not_due':
            return reason_text('not_due')                 # v2.8 §7.4: the whole sentence
        return f'跳过：{reason_text(reason)}' if reason else '跳过'
    if status == 'failed':
        return f'失败：{reason_text(reason)}' if reason else '失败'
    template = TEMPLATES.get(r['stage'])
    if template is None:
        return UNKNOWN_STAGE_NOTE
    failures = [] if r['stage'] == 'sec_monitor' else r['failures']     # its sentence names the failed tickers itself
    return template(r) + _suffixes(r['skips'], failures, r['degradations'])


def describe(row: dict, *, title: str, purpose: str) -> dict:
    """One ``stage_runs`` row as the §7.1 stage view. Pure; numbers are the row's own values."""
    r = {**row, 'skips': list(row.get('skips') or []), 'failures': list(row.get('failures') or []),
         'degradations': list(row.get('degradations') or []), 'outputs': row.get('outputs') or {},
         'detail': row.get('detail') or {}}
    return {
        'id': r['stage'], 'title': title, 'purpose': purpose,
        'status': r['status'], 'status_text': STATUS_TEXT.get(r['status'], r['status']),
        'sentence': _sentence(r),
        'input_count': r['input_count'], 'output_count': r['output_count'],
        'skips': [{'reason': s.get('reason'), 'text': reason_text(s.get('reason')), 'count': s.get('count'),
                   'samples': list(s.get('samples') or [])} for s in r['skips']],
        'failures': r['failures'],
        'degradations': [{'kind': d.get('kind'), 'text': reason_text(d.get('kind')), 'detail': d.get('detail')}
                         for d in r['degradations']],
        'reason': r.get('reason'), 'reason_text': reason_text(r.get('reason')),
        'duration_ms': r.get('duration_ms'),
        'concepts': list(STAGE_CONCEPTS.get(r['stage'], ())),
    }


def _placeholder(stage_id: str, title: str, purpose: str, *, status: str, sentence: str) -> dict:
    return {'id': stage_id, 'title': title, 'purpose': purpose, 'status': status, 'status_text': STATUS_TEXT[status],
            'sentence': sentence, 'input_count': None, 'output_count': None, 'skips': [], 'failures': [],
            'degradations': [], 'reason': None, 'reason_text': None, 'duration_ms': None,
            'concepts': list(STAGE_CONCEPTS.get(stage_id, ()))}


# ---------------------------------------------------------------- runs of a session

def _iso(moment: Any) -> Any:
    return moment.isoformat() if isinstance(moment, (date, datetime)) else moment


def _is_closed(rows: list[dict]) -> bool:
    """A closed-market dry run: every stage before ``push`` is skipped with ``not_trading_day`` (D10)."""
    ids = stages_module.STAGE_IDS_BY_VERSION.get(rows[0]['stages_version'], stages_module.STAGE_IDS) if rows else ()
    before = ids[:ids.index('push')] if 'push' in ids else tuple(i for i in ids if i != 'push')
    before_push = [r for r in rows if r['stage'] in before]
    return bool(before_push) and all(r['status'] == 'skipped' and r.get('reason') == 'not_trading_day'
                                     for r in before_push)


def _group(rows: list[dict]) -> list[dict]:
    """Runs of the session in start order (oldest first), each with its rows and summary fields."""
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r['run_id'], []).append(r)
    runs = []
    for run_id, members in grouped.items():
        first = min(members, key=lambda r: r['started_at'])
        closed = _is_closed(members)
        runs.append({
            'run_id': run_id, 'trigger': first['trigger'],
            'trigger_text': CLOSED_TEXT if closed else TRIGGER_TEXT.get(first['trigger'], first['trigger']),
            'stages_version': first['stages_version'], 'started_at': first['started_at'],
            'finished_at': max(r['finished_at'] for r in members), 'rows': len(members), 'closed': closed,
            '_members': members})
    runs.sort(key=lambda run: run['started_at'])       # stable: equal start times keep the stored order
    return runs


def _select(runs: list[dict], run_id: str | None) -> dict:
    if run_id is not None:
        for run in runs:
            if run['run_id'] == run_id:
                return run
        raise LookupError(f'运行 {run_id} 不属于这一天')
    real = [run for run in runs if not run['closed']]
    return (real or runs)[-1]


LEGACY_VERSIONS = (stages_module.STAGES_VERSION_V1, stages_module.STAGES_VERSION_V2, stages_module.STAGES_VERSION_V3)


def _display_ids(version: str) -> tuple[str, ...]:
    """The stage list a run of this version is drawn with: the v2.7 nine for ``stages-1`` .. ``stages-3`` (the two older
    ones show their missing stages as ``not_recorded``), otherwise the run's own registered order."""
    if version in LEGACY_VERSIONS:
        return stages_module.STAGE_IDS_V3
    return stages_module.STAGE_IDS_BY_VERSION.get(version, stages_module.STAGE_IDS)


def _stage_def(stage_id: str, version: str) -> stages_module.StageDef:
    if version in LEGACY_VERSIONS and stage_id in stages_module.LEGACY_STAGES:
        return stages_module.LEGACY_STAGES[stage_id]
    return next(s for s in stages_module.STAGES if s.id == stage_id)


def _effective(rows: list[dict]) -> dict[str, dict]:
    """Per stage the row with the highest ``attempt``; rows without the field keep their order (the last one wins)."""
    chosen: dict[str, dict] = {}
    for r in rows:
        known = chosen.get(r['stage'])
        if known is None or int(r.get('attempt') or 1) >= int(known.get('attempt') or 1):
            chosen[r['stage']] = r
    return chosen


def _attempt_counts(rows: list[dict]) -> dict[str, int]:
    """How often each stage was booked (its highest ``attempt``, or its row count when rows carry none)."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r['stage']] = max(counts.get(r['stage'], 0) + 1, int(r.get('attempt') or 0))
    return counts


def _unfinished(run: dict) -> bool:
    """The run has no row for its version's last stage and everything missing comes after the last recorded stage.

    ``StageRecorder`` commits a stage's row as the stage ends, so a batch still running (or killed midway) looks
    like this; a gap before a recorded stage is a bookkeeping failure instead."""
    recorded = {r['stage'] for r in run['_members']}
    ids = stages_module.STAGE_IDS_BY_VERSION.get(run['stages_version'], stages_module.STAGE_IDS)
    if not ids or ids[-1] in recorded:
        return False
    order = list(ids)
    last_recorded = max((order.index(stage) for stage in recorded if stage in order), default=-1)
    missing = [order.index(stage) for stage in ids if stage not in recorded]
    return bool(missing) and all(index > last_recorded for index in missing)


def _stage_views(run: dict) -> list[dict]:
    members = _effective(run['_members'])
    version = run['stages_version']
    ids = stages_module.STAGE_IDS_BY_VERSION.get(version, stages_module.STAGE_IDS)
    registry = {s.id for s in stages_module.STAGES}
    missing_note = UNFINISHED_NOTE if _unfinished(run) else MISSING_NOTE
    views = []
    for stage_id in _display_ids(version):
        stage = _stage_def(stage_id, version)
        if stage.id in members:
            views.append(describe(members[stage.id], title=stage.title, purpose=stage.purpose))
        elif stage.id not in ids and version in (stages_module.STAGES_VERSION_V1, stages_module.STAGES_VERSION_V2):
            if stage.id == 'review':
                note = STAGES_1_NOTE
            else:                                  # retro: which note depends on the run's own version
                note = STAGES_1_RETRO_NOTE if version == stages_module.STAGES_VERSION_V1 else STAGES_2_NOTE
            views.append(_placeholder(stage.id, stage.title, stage.purpose, status='not_recorded', sentence=note))
        else:
            views.append(_placeholder(stage.id, stage.title, stage.purpose, status='missing', sentence=missing_note))
    for stage_id, r in members.items():
        if stage_id not in registry:
            views.append({**describe(r, title=stage_id, purpose=''), 'sentence': UNKNOWN_STAGE_NOTE})
    return views


def _duration_text(seconds: int) -> str:
    minutes, rest = divmod(max(seconds, 0), 60)
    return f'{minutes} 分 {rest} 秒' if minutes else f'{rest} 秒'


def _stage_title(stage_id: str, version: str) -> str:
    try:
        return _stage_def(stage_id, version).title
    except StopIteration:
        return stage_id


def _headline(run: dict, stages: list[dict]) -> str:
    if run['closed']:
        return CLOSED_HEADLINE
    version = run['stages_version']
    members = _effective(run['_members'])
    counts = _attempt_counts(run['_members'])
    resumable = version in stages_module.STAGE_IDS_BY_VERSION and version not in LEGACY_VERSIONS
    executions = max(counts.values(), default=1) if resumable else 1
    failed = next((s for s in stages if s['status'] == 'failed'), None)
    if failed is not None and resumable:
        # v2.8 §7.4: "<date>的运行在「<节点>」失败（已自动重试 1 次）：<原因>；其后 <n> 个节点未执行"
        day = run['_members'][0]['session']
        row = members.get(failed['id']) or {}
        retried = bool(_get(row.get('detail'), 'retry'))
        after = sum(1 for s in stages if s['status'] == 'skipped' and s.get('reason') == 'upstream_failed')
        text = (f"{day.month} 月 {day.day} 日的运行在「{failed['title']}」失败"
                f"{'（已自动重试 1 次）' if retried else ''}：{failed['reason_text'] or failed['reason'] or '未知'}；"
                f"其后 {after} 个节点未执行")
    else:
        started = run['started_at'].astimezone(SH).strftime('%H:%M')
        seconds = int((run['finished_at'] - run['started_at']).total_seconds())
        shown = [f"{text} {n}" for status, text in STATUS_TEXT.items()
                 if (n := sum(1 for s in stages if s['status'] == status))]
        suffix = RUNNING_SUFFIX if _unfinished(run) else ''
        text = (f"{run['trigger_text']}运行 · {started} 开始 · 用时 {_duration_text(seconds)} · "
                f"{len(stages)} 个阶段：{' · '.join(shown)}{suffix}")
    if executions > 1:
        # the node the last resume started from: the first stage (in order) booked that many times
        ids = stages_module.STAGE_IDS_BY_VERSION.get(version, stages_module.STAGE_IDS)
        node = next((i for i in ids if counts.get(i) == executions), None)
        text += f"（第 {executions} 次执行，从「{_stage_title(node, version)}」续跑）" if node else ''
    return text


def build(rows: list[dict], *, session: date, run_id: str | None, ready: bool) -> dict:
    """The §7.1 overview of one session from its ``stage_runs`` rows (all runs of the day). Pure.

    ``run_id`` not among the day's runs -> LookupError."""
    out: dict[str, Any] = {'session': session.isoformat(), 'ready': ready, 'note': None, 'runs': [],
                           'selected_run_id': None, 'headline': None, 'stages': []}
    if not ready:
        return {**out, 'note': NOT_READY_NOTE}
    if not rows:
        if run_id is not None:
            raise LookupError(f'运行 {run_id} 不属于这一天')
        return {**out, 'note': NO_ROWS_NOTE}
    runs = _group(rows)
    chosen = _select(runs, run_id)
    stages = _stage_views(chosen)
    return {**out, 'selected_run_id': chosen['run_id'], 'headline': _headline(chosen, stages), 'stages': stages,
            'runs': [{**{k: v for k, v in run.items() if not k.startswith('_')},
                      'started_at': _iso(run['started_at']), 'finished_at': _iso(run['finished_at'])}
                     for run in runs]}


# ---------------------------------------------------------------- use case and CLI

def overview(*, session: date | None, run_id: str | None, now: datetime) -> dict:
    """Read-only use case (spec §7.1): ``session`` defaults to the latest closed session at ``now``.

    LookupError when ``run_id`` is not a run of that day; ``service.Unavailable`` without a database."""
    from investment_assistant.data.adapters import bars

    day = session if session is not None else bars.latest_closed_session(now)
    with core_tx.read_only_tx() as conn:
        ready = core_schema.stage_runs_ready(conn)
        rows = workflow_repo.stage_runs_for_session(conn, day) if ready else []
    return build(rows, session=day, run_id=run_id, ready=ready)


def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'日期格式应为 YYYY-MM-DD：{text}') from None


def _count(value: Any) -> str:
    return '-' if value is None else str(value)


def _lines(view: dict) -> list[str]:
    return [f"{s['title']} · {s['status_text']} · {s['sentence']} · {_count(s['input_count'])}/{_count(s['output_count'])}"
            for s in view['stages']]


def main(argv: list[str] | None = None) -> int:
    """Print the overview of one session, one line per stage: ``阶段 · 状态 · 句子 · I/O``.

    Exit 1 without stage evidence, 2 for bad arguments (an unknown ``--run-id`` included) or an unavailable
    database. Read-only."""
    parser = argparse.ArgumentParser(prog='python -m investment_assistant.views.runs.overview',
                                     description='某个交易日的批处理运行一览（只读）。')
    parser.add_argument('--session', type=_date_arg, required=True, help='交易日，YYYY-MM-DD')
    parser.add_argument('--run-id', help='同日多次运行时指定其中一次')
    args = parser.parse_args(argv)

    try:
        view = overview(session=args.session, run_id=args.run_id, now=datetime.now(UTC))
    except service.Unavailable as exc:
        print(f'数据库不可用：{exc}', file=sys.stderr)
        return 2
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not view['stages']:
        print(view['note'])
        return 1
    print(view['headline'])
    for line in _lines(view):
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
