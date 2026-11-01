"""Stage registry and the stage recorder for workbench runs (v2.4 §4, §5, §6.2; v2.5 §7.1).

``STAGES`` is the static registry (its order is the display order, same shape as
``families.FAMILIES``); ``StageRecorder`` appends one ``stage_runs`` row per
stage, each in its own transaction, so a later stage's failure cannot lose an
earlier stage's evidence. Write failures are swallowed into ``close``'s report —
bookkeeping must never fail the batch.

``stages-2`` (v2.5) has eight stages, ``review`` between ``agent`` and ``push``; ``stages-3`` (v2.7) has nine,
``retro`` between ``review`` and ``push``; ``stages-4`` (v2.8) has ten: ``sec_monitor`` in front and ``retro`` after
``push``. ``stages-5`` inserts ``accounts`` between ``review`` and ``push`` (eleven stages). A run against a database without migration 026 is booked as ``stages-1`` (D22): the recorder then knows
only the seven v2.4 stages and ignores ``review`` and ``retro``; without migration 027 as ``stages-2`` (v2.7 D29),
which ignores ``retro``. ``stages-1`` .. ``stages-4`` remain registered for historical runs.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from investment_assistant.workbench import collect, families, repository

STAGES_VERSION = 'stages-5'
STAGES_VERSION_V4 = 'stages-4'
STAGES_VERSION_V3 = 'stages-3'
STAGES_VERSION_V2 = 'stages-2'
STAGES_VERSION_V1 = 'stages-1'
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
)

# stages-1 .. stages-4 are history: their orders are literal tuples, never derived from STAGES (spec §4.4).
STAGE_IDS_V4: tuple[str, ...] = ('sec_monitor', 'collect', 'screen', 'regime', 'signals', 'plan', 'agent',
                                 'review', 'push', 'retro')
STAGE_IDS_V3: tuple[str, ...] = ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'retro', 'push')
STAGE_IDS_V2: tuple[str, ...] = ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'review', 'push')
STAGE_IDS_V1: tuple[str, ...] = ('collect', 'screen', 'regime', 'signals', 'plan', 'agent', 'push')
STAGE_IDS: tuple[str, ...] = tuple(stage.id for stage in STAGES)
STAGE_IDS_BY_VERSION: dict[str, tuple[str, ...]] = {STAGES_VERSION_V1: STAGE_IDS_V1, STAGES_VERSION_V2: STAGE_IDS_V2,
                                                    STAGES_VERSION_V3: STAGE_IDS_V3, STAGES_VERSION_V4: STAGE_IDS_V4,
                                                    STAGES_VERSION: STAGE_IDS}

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
        reason = families.sanitize_reason(reason)
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
        self.failures.append({'target': target, 'reason': families.sanitize_reason(reason)})

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


class StageRecorder:
    """Append one stage_runs row per stage of one workbench run (spec §6.2).

    Every write goes through the injected ``writer`` (default: its own
    transaction via ``write_stage_row``); any write error is swallowed into the
    first ``error`` and never raised, so accounting cannot fail the batch.

    ``attempt`` / ``job`` (v2.8, migration 028) are written into every row: a first run is ``attempt = 1``, a resumed
    ``daily`` run appends ``attempt = n + 1`` rows for the nodes it re-executes; the v2.7 batch keeps
    ``job = 'workbench'``. ``duration_ms`` is the stage's real wall span (``finished_at - started_at``, D25: waits
    and retries included); the accumulator's own sub-timings stay in ``detail.timings.measured_ms``.

    ``version`` picks the stage set: ``stages-4`` (default, ten rows, the ``daily`` workflow), ``stages-3`` (nine
    rows), ``stages-2`` (eight rows, migration 027 missing) or ``stages-1`` (seven rows, migration 026 missing): a
    stage outside the set is ignored by ``open`` / ``finish`` / ``skip``."""

    def __init__(self, run_id: str, trigger: str, session: date | None, *,
                 writer: Callable[[dict], None] | None = None,
                 wall_now: Callable[[], datetime] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 version: str = STAGES_VERSION,
                 attempt: int = 1,
                 job: str = 'workbench'):
        if version not in STAGE_IDS_BY_VERSION:
            raise ValueError(f'unknown stages version: {version!r}')
        self.version = version
        self.attempt = attempt        # a resumed `daily` run writes attempt n + 1 for the nodes it re-executes
        self.job = job
        self.stage_ids = STAGE_IDS_BY_VERSION[version]
        self.run_id = run_id
        self.trigger = trigger
        self.session = session
        self._writer = write_stage_row if writer is None else writer
        # looked up at call time: collect -> families -> service -> stages is an import cycle
        self._wall_now = collect.wall_clock if wall_now is None else wall_now
        self._clock = clock
        self._accs: dict[str, StageAcc] = {}
        self._started_at: dict[str, datetime] = {}
        self._written: set[str] = set()
        self.written = 0
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        """The first swallowed write error (None when every row was written)."""
        return self._error

    def open(self, stage_id: str) -> StageAcc:
        """Start a stage: keep its accumulator and its first started_at. A stage outside this version
        gets a throwaway accumulator, so callers account as usual and nothing is written."""
        if stage_id not in self.stage_ids:
            return StageAcc(self._clock)
        acc = self._accs.get(stage_id)
        if acc is None:
            acc = StageAcc(self._clock)
            self._accs[stage_id] = acc
        self._started_at.setdefault(stage_id, self._wall_now())
        return acc

    def finish(self, stage_id: str, *, failed: str | None = None) -> None:
        """Freeze the accumulator into one row and write it; a second call is ignored."""
        if stage_id in self._written or stage_id not in self.stage_ids:
            return
        self._written.add(stage_id)
        acc = self._accs.get(stage_id)
        reason = families.sanitize_reason(failed) if failed else None
        status = classify(executed=True, failed_reason=failed,
                          failures=acc.failures if acc else [],
                          degradations=acc.degradations if acc else [])
        self._write(self._row(stage_id, status=status, reason=reason, acc=acc,
                              started_at=self._started_at.get(stage_id) or self._wall_now(),
                              finished_at=self._wall_now()))

    def restart(self, stage_id: str) -> StageAcc:
        """A new accumulator for the same stage, keeping its first ``started_at`` (the node's retry, v2.8 §5.2)."""
        if stage_id not in self.stage_ids:
            return StageAcc(self._clock)
        self._accs[stage_id] = StageAcc(self._clock)
        self._started_at.setdefault(stage_id, self._wall_now())
        return self._accs[stage_id]

    def has_row(self, stage_id: str) -> bool:
        """The stage's row was already written (or its write was attempted) in this recorder."""
        return stage_id in self._written

    @contextmanager
    def stage(self, stage_id: str) -> Iterator[StageAcc]:
        """open + timing + finish; an exception is written as failed/aborted, then re-raised."""
        acc = self.open(stage_id)
        try:
            with acc.timing():
                yield acc
        except BaseException as exc:
            self.finish(stage_id, failed=f'aborted: {exc}')
            raise
        self.finish(stage_id)

    def skip(self, stage_id: str, reason: str) -> None:
        """Record a whole-stage skip (explicit_pool、not_trading_day…) as a zero row."""
        if stage_id in self._written or stage_id not in self.stage_ids:
            return
        self._written.add(stage_id)
        now = self._wall_now()
        self._write(self._row(stage_id, status='skipped', reason=families.sanitize_reason(reason),
                              acc=None, started_at=now, finished_at=now))

    def skip_many(self, stage_ids: Iterable[str], reason: str) -> None:
        for stage_id in stage_ids:
            self.skip(stage_id, reason)

    def close(self, exc: BaseException | None = None) -> dict[str, Any]:
        """Fill every unwritten stage, then report ``{'written', 'error'}`` (spec §6.2).

        With ``exc`` the leftovers are failed/``aborted: …``, without it
        skipped/``not_reached`` — each run_id ends up with exactly one row per stage of its version
        (nine for stages-3, eight for stages-2, seven for stages-1); the ``daily`` graph does not use it (its
        executor writes ``upstream_failed`` rows instead, v2.8 D8)."""
        for stage_id in self.stage_ids:
            if stage_id in self._written:
                continue
            if exc is None:
                self.skip(stage_id, 'not_reached')
            else:
                self._written.add(stage_id)
                now = self._wall_now()
                self._write(self._row(stage_id, status='failed',
                                      reason=families.sanitize_reason(f'aborted: {exc}'),
                                      acc=None, started_at=now, finished_at=now))
        return {'written': self.written, 'error': self._error}

    def _row(self, stage_id: str, *, status: str, reason: str | None, acc: StageAcc | None,
             started_at: datetime, finished_at: datetime) -> dict[str, Any]:
        """One stage_runs row: the §5.1 columns plus ``attempt``, without id / created_at."""
        detail = dict(acc.detail) if acc else {}
        if acc:
            detail['timings'] = {**(detail.get('timings') if isinstance(detail.get('timings'), dict) else {}),
                                 'measured_ms': round(acc.duration_ms)}
        return {
            'run_id': self.run_id, 'job': self.job, 'trigger': self.trigger,
            'session': self.session, 'stage': stage_id, 'stages_version': self.version, 'attempt': self.attempt,
            'status': status, 'reason': reason,
            'input_count': acc.input_count if acc else 0,
            'output_count': acc.output_count if acc else 0,
            'skips': acc.skips if acc else [],
            'failures': acc.failures if acc else [],
            'degradations': acc.degradations if acc else [],
            'outputs': acc.outputs if acc else {},
            'detail': detail,
            'started_at': started_at, 'finished_at': finished_at,
            'duration_ms': max(0, round((finished_at - started_at).total_seconds() * 1000)),
        }

    def _write(self, row: dict[str, Any]) -> None:
        try:
            self._writer(row)
        except Exception as exc:          # 记账失败不抛出，只汇总（spec §6.2）
            if self._error is None:
                self._error = families.sanitize_reason(exc)
        else:
            self.written += 1


def write_stage_row(row: dict[str, Any]) -> None:
    """Append one stage row in its own transaction (pattern of collect.write_runs)."""
    from investment_assistant.workbench import service as service_module

    with service_module._tx() as conn:
        repository.insert_stage_run(conn, row)
