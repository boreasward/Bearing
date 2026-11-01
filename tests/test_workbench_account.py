"""Account snapshots and cash flows: append-only, replay-safe, void-once (design §3.4, §3.5)."""
import investment_assistant.execution.repo as execution_repo
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from investment_assistant.execution import account_records as account
from investment_assistant.execution import ledger

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)


def snapshot_payload(**overrides):
    payload = {'request_id': str(uuid.uuid4()), 'as_of': '2026-10-31', 'equity': '100000', 'note': None}
    payload.update(overrides)
    return payload


def flow_payload(**overrides):
    payload = {'request_id': str(uuid.uuid4()), 'flow_date': '2026-10-21', 'amount': '5000', 'note': '入金'}
    payload.update(overrides)
    return payload


def test_snapshot_replay_same_request(workbench_db):
    payload = snapshot_payload()
    first = account.record_snapshot(workbench_db, payload, now=NOW)
    again = account.record_snapshot(workbench_db, payload, now=NOW + timedelta(minutes=1))
    assert first['status'] == 'created' and again['status'] == 'existing'
    assert again['record']['id'] == first['record']['id']


def test_snapshot_replay_conflict(workbench_db):
    payload = snapshot_payload()
    account.record_snapshot(workbench_db, payload, now=NOW)
    with pytest.raises(ledger.Conflict):
        account.record_snapshot(workbench_db, {**payload, 'equity': '110000'}, now=NOW)


def test_snapshot_correction_must_target_head(workbench_db):
    s1 = account.record_snapshot(workbench_db, snapshot_payload(as_of='2026-09-30'), now=NOW)['record']
    s2 = account.record_snapshot(workbench_db, snapshot_payload(as_of='2026-10-31'),
                                   now=NOW + timedelta(hours=1))['record']
    with pytest.raises(ledger.Conflict):  # s1 已不是当前有效快照
        account.record_snapshot(workbench_db, snapshot_payload(as_of='2026-10-31', supersedes_id=str(s1['id'])),
                                now=NOW)
    fix = account.record_snapshot(workbench_db, snapshot_payload(as_of='2026-10-31', equity='110000',
                                                                 supersedes_id=str(s2['id'])), now=NOW)
    assert fix['status'] == 'created'
    assert execution_repo.snapshot_at(workbench_db, date(2026, 12, 31), cutoff_at=NOW)['equity'] == Decimal('110000')


def test_flow_zero_rejected(workbench_db):
    with pytest.raises(ledger.LedgerError):
        account.record_flow(workbench_db, flow_payload(amount='0'), now=NOW)
    with pytest.raises(ledger.LedgerError):
        account.record_flow(workbench_db, flow_payload(amount='abc'), now=NOW)
    assert account.record_flow(workbench_db, flow_payload(amount='-3000'), now=NOW)['status'] == 'created'


def test_flow_replay_and_void_once(workbench_db):
    payload = flow_payload(amount='-3000')
    first = account.record_flow(workbench_db, payload, now=NOW)
    again = account.record_flow(workbench_db, payload, now=NOW + timedelta(hours=1))
    assert first['status'] == 'created' and again['status'] == 'existing'
    voided = account.void_flow(workbench_db, first['record']['id'], '录错方向', now=NOW)
    assert voided['voided_at'] is not None
    with pytest.raises(ledger.Conflict):
        account.void_flow(workbench_db, first['record']['id'], '再作废一次', now=NOW)
    with pytest.raises(ledger.NotFound):
        account.void_flow(workbench_db, uuid.uuid4(), 'x', now=NOW)
    with pytest.raises(ledger.LedgerError):
        account.void_flow(workbench_db, first['record']['id'], '  ', now=NOW)


def test_snapshot_at_ignores_recorded_after_cutoff(workbench_db):
    t1 = datetime(2026, 10, 22, 22, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
    account.record_snapshot(workbench_db, snapshot_payload(as_of='2026-10-15', equity='100000'), now=t1)
    account.record_snapshot(workbench_db, snapshot_payload(as_of='2026-10-20', equity='105000'), now=t2)  # 补录
    seen = execution_repo.snapshot_at(workbench_db, date(2026, 10, 21), cutoff_at=datetime(2026, 10, 23, tzinfo=UTC))
    assert seen['equity'] == Decimal('100000')  # 补录行 recorded_at 晚于 cutoff，被忽略
    assert execution_repo.snapshot_at(workbench_db, date(2026, 10, 21), cutoff_at=t2)['equity'] == Decimal('105000')
    assert execution_repo.snapshot_at(workbench_db, date(2026, 10, 14), cutoff_at=t2) is None
