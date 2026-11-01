"""Stage recorder for workbench runs (v2.4 §4, §5, §6.2; v2.5 §7.1).

The registry definitions (``StageDef``, ``STAGES``, ``STAGE_IDS*``, ``classify``,
``StageAcc``) moved to ``investment_assistant/core/stages.py`` in Task 0 and are
re-exported below until Task 1; what stays here is the write side — ``StageRecorder``
appends one ``stage_runs`` row per stage, each in its own transaction, so a later
stage's failure cannot lose an earlier stage's evidence. Write failures are swallowed
into ``close``'s report — bookkeeping must never fail the batch.

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
from datetime import date, datetime
from typing import Any

from investment_assistant.core.stages import (LEGACY_STAGES as LEGACY_STAGES, SAMPLES_MAX as SAMPLES_MAX,
                                              STAGES as STAGES, STAGE_IDS as STAGE_IDS,
                                              STAGE_IDS_BY_VERSION as STAGE_IDS_BY_VERSION,
                                              STAGE_IDS_V1 as STAGE_IDS_V1, STAGE_IDS_V2 as STAGE_IDS_V2,
                                              STAGE_IDS_V3 as STAGE_IDS_V3, STAGE_IDS_V4 as STAGE_IDS_V4,
                                              STAGES_VERSION as STAGES_VERSION, STAGES_VERSION_V1 as STAGES_VERSION_V1,
                                              STAGES_VERSION_V2 as STAGES_VERSION_V2,
                                              STAGES_VERSION_V3 as STAGES_VERSION_V3,
                                              STAGES_VERSION_V4 as STAGES_VERSION_V4,
                                              STAGES_VERSION_V5 as STAGES_VERSION_V5,
                                              StageAcc as StageAcc, StageDef as StageDef, classify as classify)
from investment_assistant.workbench import collect, families, repository
from investment_assistant.core import tx as core_tx


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
    with core_tx._tx() as conn:
        repository.insert_stage_run(conn, row)
