"""Stage registry definitions (v2.4 §4, §5, §6.2; v2.5 §7.1) — the read-only half of ``stages``.

``STAGES`` is the static registry (its order is the display order, same shape as
``families.FAMILIES``); the write-side recorder (``StageRecorder``, ``write_stage_row``)
stays in ``workbench/stages.py`` until Task 1 moves it to ``workflow/recorder.py``.

``stages-2`` (v2.5) has eight stages, ``review`` between ``agent`` and ``push``; ``stages-3`` (v2.7) has nine,
``retro`` between ``review`` and ``push``; ``stages-4`` (v2.8) has ten: ``sec_monitor`` in front and ``retro`` after
``push``. ``stages-5`` inserts ``accounts`` between ``review`` and ``push`` (eleven stages); ``stages-6`` (v2.14/v2.15)
adds ``portfolio`` between ``review`` and ``accounts`` and ``propose`` after ``retro`` (thirteen stages). A run against a database without migration 026 is booked as ``stages-1`` (D22): the recorder then knows
only the seven v2.4 stages and ignores ``review`` and ``retro``; without migration 027 as ``stages-2`` (v2.7 D29),
which ignores ``retro``. ``stages-1`` .. ``stages-5`` remain registered for historical runs.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from investment_assistant.core.redact import sanitize_reason
from investment_assistant.core.versions import (STAGES_VERSION_V1, STAGES_VERSION_V2, STAGES_VERSION_V3,
                                                STAGES_VERSION_V4, STAGES_VERSION_V5)

# The registry's current version (13 stages); ``core.versions.STAGES_VERSION`` carries the same value.
STAGES_VERSION = 'stages-6'
SAMPLES_MAX = 10                      # per skip-reason sample cap (spec §5.1)


@dataclass(frozen=True)
class StageDef:
    id: str
    title: str
    purpose: str


STAGES: tuple[StageDef, ...] = (
    StageDef(
        id='sec_monitor',
        title='SEC 监控',
        purpose='扫描观察名单的 SEC 申报，归档原文、提取正文事实并经 Discord Bot 直发通知；已通知过的不重发。',
    ),
    StageDef(
        id='collect',
        title='数据采集',
        purpose='逐只采集日线、资讯标题、财报日、SEC 列表与 FRED 宏观，是后面各阶段的数据底座。',
    ),
    StageDef(
        id='screen',
        title='筛选 `screen-1`',
        purpose='在纳指 100 范围内按 `screen-1` 过滤排序，入选成员自动并入扩展观察池。',
    ),
    StageDef(
        id='regime',
        title='市场环境 `regime-2`',
        purpose='用范围 ∪ 手动池的广度与宏观序列判定 `regime-2` 区制，并计算行业强弱。',
    ),
    StageDef(
        id='signals',
        title='信号与候选',
        purpose='规则 v1 在观察池与持仓上逐只评估，产生信号候选并合成每股每日一个决策单元。',
    ),
    StageDef(
        id='plan',
        title='规则计划 `plan-1`',
        purpose='为无冲突、未决定的买入单元生成 `plan-1` 计划：ATR 止损、2R 目标、按风险定仓、超限作废。',
    ),
    StageDef(
        id='agent',
        title='Agent 建议',
        purpose='Agent 轨道对候选做提议与反方论证并给出解读；缺 key 等不可用情况整阶段跳过。',
    ),
    StageDef(
        id='review',
        title='每日复查',
        purpose='对每份有效 Agent 计划做一次复查：维持、修订或作废；系统信号与你的输入触发认真复查。',
    ),
    StageDef(
        id='portfolio',
        title='组合检查',
        purpose='对真实账户和每个活跃模拟账户做组合硬检查（行业集中、单票与总敞口、现金先后），真实账户另由组合经理写取舍说明；开关关闭时整段跳过。',
    ),
    StageDef(
        id='accounts',
        title='账户',
        purpose='结算模拟账户昨天的挂单与退出，给每个账户记一行当日权益，再由照计划执行账户机械挂单、Agent 自管账户决定明天买卖什么。',
    ),
    StageDef(
        id='push',
        title='复核与推送',
        purpose='组装今日视图并把摘要推送 Discord；手动运行不推送。',
    ),
    StageDef(
        id='retro',
        title='复盘',
        purpose='交易日检测已结束的计划与平仓并起草复盘卡；周 / 月复盘到期时生成并推送。',
    ),
    StageDef(
        id='propose',
        title='优化提案',
        purpose='周六根据运行证据起草至多三条优化提案，写进 roadmap 等你决定；非周六或开关关闭时跳过。',
    ),
)

# stages-1 .. stages-5 are history: their orders are literal tuples, never derived from STAGES (spec §4.4).
STAGE_IDS_V5: tuple[str, ...] = ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review',
                                 'accounts', 'push', 'retro')
STAGE_IDS_V4: tuple[str, ...] = ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent',
                                 'review', 'push', 'retro')
STAGE_IDS_V3: tuple[str, ...] = ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'retro', 'push')
STAGE_IDS_V2: tuple[str, ...] = ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push')
STAGE_IDS_V1: tuple[str, ...] = ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'push')
STAGE_IDS: tuple[str, ...] = tuple(stage.id for stage in STAGES)
STAGE_IDS_BY_VERSION: dict[str, tuple[str, ...]] = {STAGES_VERSION_V1: STAGE_IDS_V1, STAGES_VERSION_V2: STAGE_IDS_V2,
                                                    STAGES_VERSION_V3: STAGE_IDS_V3, STAGES_VERSION_V4: STAGE_IDS_V4,
                                                    STAGES_VERSION_V5: STAGE_IDS_V5, STAGES_VERSION: STAGE_IDS}

# What a stage was called before ``stages-4`` (display of historic runs only): ``retro`` was the retro-card stage.
LEGACY_STAGES: dict[str, StageDef] = {
    'retro': StageDef(id='retro', title='复盘卡', purpose='检测已结束的计划与平仓，冻结复盘事实并起草复盘卡'),
}


def classify(*, executed: bool, failed_reason: str | None, failures: list,
             degradations: list) -> str:
    """Map one stage's accounting to its stage_runs status (spec §5.1 判定).

    Precedence: skipped > failed > partial > degraded > success."""
    if not executed:
        return 'skipped'
    if failed_reason:
        return 'failed'
    if failures:
        return 'partial'
    if degradations:
        return 'degraded'
    return 'success'


class StageAcc:
    """One stage's running accounting, frozen into a stage_runs row by the recorder.

    Account next to the work, not by re-parsing summary strings (same idea as
    ``collect.Tally``): input/output counts, grouped skips, per-target failures,
    degradations, output references, free-form ``detail``, and a monotonic-clock
    duration accumulated by ``timing`` blocks."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.input_count = 0
        self.output_count = 0
        self.skips: list[dict[str, Any]] = []      # [{'reason', 'count', 'samples'}]
        self.failures: list[dict[str, Any]] = []   # [{'target', 'reason'}]
        self.degradations: list[dict[str, Any]] = []   # [{'kind', 'detail'}]
        self.outputs: dict[str, Any] = {}
        self.detail: dict[str, Any] = {}
        self.duration_ms = 0.0
        self._clock = clock
        self._skip_groups: dict[str, dict[str, Any]] = {}

    def input(self, n: int = 1) -> None:
        self.input_count += n

    def output(self, n: int = 1) -> None:
        self.output_count += n

    def skip(self, reason: str, sample: str | None = None) -> None:
        """One skipped item: same reasons merge, samples stay unique and capped."""
        reason = sanitize_reason(reason)
        group = self._skip_groups.get(reason)
        if group is None:
            group = {'reason': reason, 'count': 0, 'samples': []}
            self._skip_groups[reason] = group
            self.skips.append(group)
        group['count'] += 1
        if sample is not None and len(group['samples']) < SAMPLES_MAX \
                and sample not in group['samples']:
            group['samples'].append(sample)

    def fail(self, target: str, reason: Any) -> None:
        self.failures.append({'target': target, 'reason': sanitize_reason(reason)})

    def degrade(self, kind: str, detail: Any = '') -> None:
        self.degradations.append({'kind': kind, 'detail': detail})

    def ref(self, key: str, value: Any) -> None:
        """Append to the outputs list under ``key`` (ids gathered one by one)."""
        self.outputs.setdefault(key, []).append(value)

    def set_ref(self, key: str, value: Any) -> None:
        """Set a scalar output reference."""
        self.outputs[key] = value

    @contextmanager
    def timing(self) -> Iterator[StageAcc]:
        """Accumulate this block's monotonic elapsed time into ``duration_ms``."""
        start = self._clock()
        try:
            yield self
        finally:
            self.duration_ms += (self._clock() - start) * 1000

    def freeze(self) -> dict[str, Any]:
        """The accounting as plain JSON-able data (a checkpoint carries it from one node to the next, v2.8 §4.3)."""
        return {'input_count': self.input_count, 'output_count': self.output_count, 'skips': self.skips,
                'failures': self.failures, 'degradations': self.degradations, 'outputs': self.outputs,
                'detail': self.detail, 'duration_ms': self.duration_ms}

    def restore(self, data: dict[str, Any]) -> None:
        """Load a ``freeze`` result into this (fresh) accumulator."""
        self.input_count = int(data.get('input_count') or 0)
        self.output_count = int(data.get('output_count') or 0)
        self.skips = [dict(group) for group in data.get('skips') or []]
        self._skip_groups = {group['reason']: group for group in self.skips}
        self.failures = [dict(item) for item in data.get('failures') or []]
        self.degradations = [dict(item) for item in data.get('degradations') or []]
        self.outputs = dict(data.get('outputs') or {})
        self.detail = dict(data.get('detail') or {})
        self.duration_ms = float(data.get('duration_ms') or 0.0)
