"""Shared fixtures and fakes for the v2.8 daily-workflow tests (and Task 4's run-detail / API tests).

``daily_db`` is the ``workbench_db`` schema (``tests/conftest.WORKBENCH_MIGRATIONS`` ends with 029 since v2.9) with
``service.connection`` pointing at it. Everything else — fetchers, the scripted model, the checkpointer, ``sleep``,
the clocks, the notifier and ``job_reports`` — is a fake: nothing touches the network or a real database.
"""
from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest

from investment_assistant.core import locks as core_locks
from investment_assistant.core import tx as core_tx
from langgraph.checkpoint.memory import InMemorySaver

from investment_assistant.config import AssistantConfig
from investment_assistant.workbench import collect


class FakeClock:
    """One fake world clock: ``mono()`` (the deps clock) and ``wall()`` advance together; ``sleep`` advances both
    and records the waits, so a 60-second node retry costs nothing and still moves both clocks."""

    def __init__(self, wall: datetime = datetime(2026, 10, 22, 22, 31, tzinfo=UTC)):
        self.t = 0.0
        self.base = wall
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self.t

    def wall(self) -> datetime:
        from datetime import timedelta

        return self.base + timedelta(seconds=self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class Notices:
    """The notifier stand-in: every ``send_notifications`` call is recorded; ``result`` is what it returns."""

    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.result = {'sent': True, 'messages': 1, 'channel': 'discord'}

    def __call__(self, task, notifications, notify_cfg, *, client=None):
        self.calls.append((task, list(notifications)))
        return {**self.result, 'messages': len(notifications)}

    def titles(self, task: str | None = None) -> list[str]:
        return [n.title for t, items in self.calls if task in (None, t) for n in items]


class NotifyCfg:
    """The notify settings the push stage and the failure notice read."""
    channel = 'discord'
    discord_enabled = True
    task_enabled = {'filings': True, 'workbench': True, 'workbench_review': True}
    task_channels = {'workbench': 'signals', 'workbench_review': 'signals', 'filings': 'signals'}
    webhooks: dict = {}


def shared_saver():
    """One in-memory checkpoint saver for a whole test: ``run`` and a later ``resume`` see the same checkpoints."""
    saver = InMemorySaver()
    return saver, lambda: nullcontext(saver)


def daily_config() -> AssistantConfig:
    return AssistantConfig()


def lock_connection_factory():
    """Fresh, autocommit connections to the disposable cluster for the ``daily:run`` lock (advisory locks only)."""
    import psycopg

    url = os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']
    return lambda: psycopg.connect(url, autocommit=True)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(collect, 'wall_clock', fake.wall)
    return fake


@pytest.fixture
def notices(monkeypatch):
    from investment_assistant.notify import notifier

    fake = Notices()
    monkeypatch.setattr(notifier, 'send_notifications', fake)
    return fake


@pytest.fixture
def job_reports(monkeypatch):
    """``_harness._record`` writes into ``job_reports`` of the test schema; the rows are read back with ``rows()``."""
    from investment_assistant.tasks import _harness

    class Reports:
        def __init__(self):
            self.conn = None

        def rows(self):
            cur = self.conn.execute('SELECT task, run_id, status, trigger_source, summary, target_start_date '
                                    'FROM job_reports ORDER BY id')
            return [dict(zip(('task', 'run_id', 'status', 'trigger_source', 'summary', 'target_start_date'), r))
                    for r in cur.fetchall()]

    reports = Reports()

    def record(*, task, run_id, status, started_at, finished_at, summary, target_start_date=None,
               target_end_date=None, trigger_source='scheduled'):
        from investment_assistant.db import insert_job_report

        insert_job_report(reports.conn, task=task, run_id=run_id, status=status, started_at=started_at,
                          finished_at=finished_at, summary=summary, target_start_date=target_start_date,
                          target_end_date=target_end_date, trigger_source=trigger_source)

    monkeypatch.setattr(_harness, '_record', record)
    return reports


def _wire(workbench_db, monkeypatch, job_reports):
    from investment_assistant.tasks import daily
    from investment_assistant.workbench import plan_review, service

    from contextlib import contextmanager

    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, 'connection', connection)
    monkeypatch.setattr(plan_review, '_lock_connection', lock_connection_factory())
    monkeypatch.setattr(core_locks, '_lock_connection', lock_connection_factory())
    job_reports.conn = workbench_db
    return workbench_db


@pytest.fixture
def daily_db(workbench_db, monkeypatch, job_reports):
    """The workbench schema (``conftest.WORKBENCH_MIGRATIONS`` ends with 029), ``service.connection`` on it and the run
    locks on connections of their own."""
    return _wire(workbench_db, monkeypatch, job_reports)


@pytest.fixture
def plain_db(workbench_db, monkeypatch, job_reports):
    """The same wiring on a schema where 028 looks as if it had not run: its tables are dropped again."""
    workbench_db.execute('DROP TABLE agent_attempt_resets, agent_connection_events, agent_connections')
    workbench_db.commit()
    return _wire(workbench_db, monkeypatch, job_reports)
