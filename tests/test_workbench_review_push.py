"""Review push orchestration: the tick, manual pushes and their records (P4 design §6.1, §6.3).

SQL against a disposable schema; the Discord client is a recording fake.
"""
import investment_assistant.review.weekly as review_weekly
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import investment_assistant.review.repo as review_repo
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.core import tx as core_tx

from investment_assistant.config import NotifyConfig
import investment_assistant.core.tx as service
import investment_assistant.workflow.nodes.retro as retro_node
from investment_assistant.tasks import ports

W = date(2026, 10, 19)                 # the week of 2026-10-19
SAT_MORNING = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)   # 北京周六 08:00


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db
    monkeypatch.setattr(core_tx, 'connection', connection)
    return workbench_db


class FakeClient:
    """Discord stand-in: records (channel, payload); the first fail_first sends raise."""

    def __init__(self, fail_first=0, error='boom'):
        self.sent = []
        self.fail_first = fail_first
        self.error = error

    def send(self, channel, payload):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RuntimeError(self.error)
        self.sent.append((channel, payload))


def cfg(**overrides):
    values = {'task_channels': {'workbench_review': 'signals'},
              'task_enabled': {'workbench_review': True}}
    values.update(overrides)
    return NotifyConfig(**values)


def tick(db, now=SAT_MORNING, *, notify_cfg=None, client=None, trigger_source='scheduled'):
    return retro_node.review_tick(now=now, notify_cfg=notify_cfg or cfg(), send=ports.send, trigger_source=trigger_source,
                               client=client)


def _messages(client, marker):
    return [s for s in client.sent if marker in str(s[1])]


def _snapshot(db, *, as_of, recorded_at, equity='100000'):
    execution_repo.insert_snapshot(db, {'request_id': str(uuid.uuid4()), 'as_of': as_of.isoformat(),
                                    'equity': equity, 'note': None, 'supersedes_id': None}, 'h',
                               recorded_at=recorded_at)


def _decision(db, *, decided_at):
    execution_repo_legacy._insert_decision_for_tests(db, {'request_id': str(uuid.uuid4()), 'ticker': 'NVDA',
                                    'signal_date': decided_at.date(), 'candidate_ids': [], 'brief_id': None,
                                    'proposed_action': 'watch', 'final_action': 'watch', 'decision': 'accept',
                                    'reason': '启用判断用例', 'supersedes_id': None}, 'h', decided_at=decided_at)


# --- weekly ---

def test_tick_generates_and_pushes_weekly(db):
    client = FakeClient()
    out = tick(db, client=client)
    assert out['weekly']['action'] == 'generated'
    assert out['weekly']['push']['sent'] is True
    assert len(_messages(client, '周度复盘')) == 1
    row = db.execute('SELECT kind, period, sent, messages, trigger_source FROM review_pushes').fetchone()
    assert row == ('weekly', W, True, 1, 'scheduled')
    assert review_repo.weekly_head(db, W)['notes'] == '自动生成'


def test_tick_reuses_snapshot_generated_after_close(db):
    review_repo.insert_weekly_review(db, W, 'review-4', {'units': {'decided': 0}}, '手写', None)
    client = FakeClient()
    out = tick(db, client=client)
    assert out['weekly']['action'] == 'reused'                  # 收盘后已有快照，直接沿用
    assert out['weekly']['push']['sent'] is True
    assert db.execute('SELECT count(*) FROM weekly_reviews').fetchone()[0] == 1


def test_tick_supersedes_snapshot_generated_before_close(db):
    review_repo.insert_weekly_review(db, W, 'review-3', {'units': {'decided': 0}}, '周三', None)
    db.execute("UPDATE weekly_reviews SET created_at = '2026-10-21T22:00:00+00:00' WHERE notes = '周三'")
    client = FakeClient()
    out = tick(db, client=client)
    assert out['weekly']['action'] == 'generated'               # 收盘前的快照被更替
    head = review_repo.weekly_head(db, W)
    assert head['notes'] == '自动生成' and head['supersedes_id'] is not None
    assert out['weekly']['push']['review_id'] == head['id']     # 推送的是新快照


def test_tick_does_not_repeat_after_success(db):
    client = FakeClient()
    tick(db, client=client)
    out = tick(db, client=client)
    assert out['weekly']['action'] == 'already_sent'
    assert len(_messages(client, '周度复盘')) == 1
    assert db.execute("SELECT count(*) FROM review_pushes WHERE kind = 'weekly'").fetchone()[0] == 1


def test_tick_retries_after_send_failure(db):
    client = FakeClient(fail_first=1)
    first = tick(db, client=client)
    assert first['weekly']['push']['sent'] is False
    assert 'send_failed' in first['weekly']['push']['reason']
    second = tick(db, client=client)
    assert second['weekly']['action'] == 'reused'
    assert second['weekly']['push']['sent'] is True             # 失败后下一次定时运行重试
    assert len(_messages(client, '周度复盘')) == 1


def test_tick_task_disabled_retries_later(db):
    client = FakeClient()
    first = tick(db, notify_cfg=cfg(task_enabled={'workbench_review': False}), client=client)
    assert first['weekly']['push']['reason'] == 'task_disabled'
    assert client.sent == []
    second = tick(db, client=client)                            # 开关打开后照常推送
    assert second['weekly']['action'] == 'reused' and second['weekly']['push']['sent'] is True


# --- monthly ---

def test_monthly_waits_then_reminds_once(db):
    _snapshot(db, as_of=date(2026, 9, 30), recorded_at=datetime(2026, 10, 1, 12, tzinfo=UTC))  # 9 月已启用
    client = FakeClient()
    early = tick(db, now=datetime(2026, 11, 3, 0, 0, tzinfo=UTC), client=client)
    assert early['monthly']['action'] == 'waiting'              # 缺快照，还不到提醒日，不发送
    assert _messages(client, '月度复盘待录入') == []
    reminded = tick(db, now=datetime(2026, 11, 7, 0, 0, tzinfo=UTC), client=client)
    assert reminded['monthly']['action'] == 'reminded'
    assert len(_messages(client, '月度复盘待录入')) == 1
    row = db.execute("SELECT kind, period, sent, review_id FROM review_pushes WHERE kind = 'monthly_reminder'").fetchone()
    assert row == ('monthly_reminder', date(2026, 10, 1), True, None)
    again = tick(db, now=datetime(2026, 11, 8, 0, 0, tzinfo=UTC), client=client)
    assert again['monthly']['action'] == 'waiting'              # 每月只提醒一次
    assert len(_messages(client, '月度复盘待录入')) == 1


def test_monthly_no_reminder_before_workbench_started(db):
    """No decision and no account snapshot on or before the month's end: the month predates
    the workbench, so nothing is reminded — even on later runs."""
    client = FakeClient()
    _decision(db, decided_at=datetime(2026, 11, 2, 15, tzinfo=UTC))       # 11 月才开始使用
    _snapshot(db, as_of=date(2026, 11, 2), recorded_at=datetime(2026, 11, 2, 16, tzinfo=UTC))
    for day in (7, 8):
        out = tick(db, now=datetime(2026, 11, day, 0, 0, tzinfo=UTC), client=client)
        assert out['monthly'] == {'period': date(2026, 10, 1), 'action': 'before_start'}
    assert _messages(client, '月度复盘待录入') == []
    assert db.execute("SELECT count(*) FROM review_pushes WHERE kind = 'monthly_reminder'").fetchone()[0] == 0


def test_monthly_no_reminder_on_empty_database(db):
    client = FakeClient()
    out = tick(db, now=datetime(2026, 11, 7, 0, 0, tzinfo=UTC), client=client)
    assert out['monthly']['action'] == 'before_start'
    assert _messages(client, '月度复盘待录入') == []


def test_monthly_reminds_when_a_decision_precedes_month_end(db):
    _decision(db, decided_at=datetime(2026, 10, 30, 14, tzinfo=UTC))      # 10 月内已有决定
    client = FakeClient()
    out = tick(db, now=datetime(2026, 11, 7, 0, 0, tzinfo=UTC), client=client)
    assert out['monthly']['action'] == 'reminded'
    assert len(_messages(client, '月度复盘待录入 · 2026-10')) == 1


def test_monthly_generated_after_snapshot_recorded(db):
    _snapshot(db, as_of=date(2026, 10, 31), recorded_at=datetime(2026, 11, 1, 12, tzinfo=UTC))
    client = FakeClient()
    out = tick(db, now=datetime(2026, 11, 7, 0, 0, tzinfo=UTC), client=client)
    assert out['monthly']['action'] == 'generated'
    assert out['monthly']['push']['sent'] is True
    assert len(_messages(client, '月度复盘 · 2026-10')) == 1
    head = review_repo.monthly_head(db, date(2026, 10, 1))
    assert head['notes'] == '自动生成'


def test_monthly_head_older_than_snapshot_is_superseded(db):
    _snapshot(db, as_of=date(2026, 10, 31), recorded_at=datetime(2026, 11, 1, 12, tzinfo=UTC))
    review_repo.insert_monthly_review(db, date(2026, 10, 1), 'monthly-2', {'month': '2026-10'}, '旧', None)
    db.execute("UPDATE monthly_reviews SET created_at = '2026-10-31T20:00:00+00:00' WHERE notes = '旧'")
    client = FakeClient()
    out = tick(db, now=datetime(2026, 11, 7, 0, 0, tzinfo=UTC), client=client)
    assert out['monthly']['action'] == 'generated'              # 快照晚于旧快照生成时间，更替
    head = review_repo.monthly_head(db, date(2026, 10, 1))
    assert head['notes'] == '自动生成' and head['supersedes_id'] is not None


# --- manual pushes ---

def test_manual_push_forces_resend(db):
    client = FakeClient()
    tick(db, client=client)
    out = retro_node.push_review('weekly', W, now=SAT_MORNING, notify_cfg=cfg(), send=ports.send, client=client)
    assert out['sent'] is True                                  # 手动不受已推送限制
    assert len(_messages(client, '周度复盘')) == 2
    row = db.execute("SELECT trigger_source FROM review_pushes WHERE trigger_source = 'manual'").fetchone()
    assert row == ('manual',)


def test_manual_push_unknown_period_404(db):
    with pytest.raises(LookupError):
        retro_node.push_review('weekly', date(2026, 11, 16), now=SAT_MORNING, notify_cfg=cfg(), send=ports.send)


def test_manual_push_bad_kind_or_period():
    from investment_assistant.execution import ledger
    with pytest.raises(ledger.LedgerError):
        retro_node.push_review('daily', W, now=SAT_MORNING, notify_cfg=cfg(), send=ports.send)
    with pytest.raises(ledger.LedgerError):
        retro_node.push_review('weekly', W + timedelta(days=1), now=SAT_MORNING, notify_cfg=cfg(), send=ports.send)


def test_push_reason_redacted(db):
    secret = 'https://discord.com/api/webhooks/SECRET123'
    notify = cfg(webhooks={'signals': secret})
    client = FakeClient(fail_first=1, error=f'POST {secret} failed')
    out = tick(db, notify_cfg=notify, client=client)
    assert 'SECRET123' not in out['weekly']['push']['reason']
    assert '***' in out['weekly']['push']['reason']


def test_generation_conflict_recorded_and_retried(db, monkeypatch):
    from investment_assistant.execution import ledger
    real_generate = review_weekly.generate_weekly

    def conflicting(week_start, **kwargs):
        raise ledger.Conflict('该周已有更新的复盘；请更正最新一条')

    monkeypatch.setattr(review_weekly, 'generate_weekly', conflicting)
    client = FakeClient()
    first = tick(db, client=client)
    assert first['weekly']['action'] == 'error'
    assert any('该周已有更新' in str(e) for e in first['errors'])
    assert _messages(client, '周度复盘') == []                   # 周度出错不影响月度部分
    assert first['monthly']['action'] != 'error'
    monkeypatch.setattr(review_weekly, 'generate_weekly', real_generate)
    second = tick(db, client=client)
    assert second['weekly']['action'] == 'generated' and second['weekly']['push']['sent'] is True
