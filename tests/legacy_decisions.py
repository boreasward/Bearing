"""Test-only fixture for the retired v2.8 decision flow (v2.9 spec §6.4, D24).

``ledger.record_decision`` is gone from production code: ``POST /decisions`` answers 410 and nothing writes
``signal_decisions`` any more. The v2.5-v2.8 tests still need *old data* (a decision with its plan link,
an agent_plan_id, an own plan), so this module keeps the old recording logic, verbatim, and writes through
``execution_repo_legacy._insert_decision_for_tests``. Import it from tests only.
"""
from __future__ import annotations
from investment_assistant.execution import plan_context as execution_plan_context
from investment_assistant.execution import trades as execution_trades
import investment_assistant.core.dbutil as core_dbutil
import investment_assistant.core.schema as core_schema
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy

from datetime import date, datetime
from typing import Any

# Repository consumers use their owning layers below.
from investment_assistant.execution.ledger import (Conflict, LedgerError, _choice, _replay, _text, _ticker, _uuid,
                                                   request_hash)

ACTIONS = ('buy', 'sell', 'watch')
FINAL_ACTIONS = ('buy', 'sell', 'watch', 'ignore')
REVIEW_LAYOUTS = ('agent_first', 'legacy')
REVIEW_VIEW_KEYS = ('layout', 'baseline_expanded', 'agent_version_no')
DECISION_V25_KEYS = ('agent_plan_id', 'review_view')   # v2.5 §9.3, migration 026
MIGRATION_MISSING = '迁移 026 未执行'

class AgentPlanStale(Conflict):
    """v2.5 §9.3: the page showed an older Agent version than the chain's head (HTTP 409).

    ``str()`` is the 409 body's ``error``: the reason code, then the user-facing sentence."""

    reason = 'agent_plan_stale'

    def __init__(self, version_no: int):
        self.version_no = version_no
        self.text = f'Agent 计划已更新到第 {version_no} 版，请刷新后再决定'
        super().__init__(f'{self.reason}: {self.text}')



def _own_plan(value: Any) -> dict[str, Any] | None:
    if value in (None, ''):
        return None
    if not isinstance(value, dict):
        raise LedgerError('own_plan must be an object')
    from investment_assistant.execution import plan_levels as _plans

    try:
        levels = _plans.validate_levels(value)
    except ValueError as exc:
        raise LedgerError(str(exc)) from None
    return {'request_id': _uuid(value.get('request_id'), 'own_plan.request_id'), **levels}


def _review_view(value: Any) -> dict[str, Any] | None:
    """v2.5 §9.3: what the page reports about the layout it showed; for acceptance stats only."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LedgerError('review_view must be an object')
    extra = sorted(set(value) - set(REVIEW_VIEW_KEYS))
    if extra:
        raise LedgerError(f"review_view 不认识的字段：{', '.join(map(str, extra))}")
    layout = _choice(value.get('layout'), 'review_view.layout', REVIEW_LAYOUTS)
    expanded = value.get('baseline_expanded')
    if not isinstance(expanded, bool):
        raise LedgerError('review_view.baseline_expanded must be true or false')
    version_no = value.get('agent_version_no')
    if version_no is not None and (isinstance(version_no, bool) or not isinstance(version_no, int) or version_no < 0):
        raise LedgerError('review_view.agent_version_no must be a non-negative integer or null')
    return {'layout': layout, 'baseline_expanded': expanded, 'agent_version_no': version_no}


def decision_hash(rec: dict[str, Any]) -> str:
    """request_hash of a validated decision. The v2.5 fields enter only when given, so a request
    recorded before v2.5 still replays to the same hash."""
    return request_hash({k: v for k, v in rec.items() if k not in DECISION_V25_KEYS or v is not None})


def validate_decision(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        signal_date = date.fromisoformat(str(payload.get('signal_date')))
    except ValueError:
        raise LedgerError('signal_date must be YYYY-MM-DD') from None
    candidate_ids = payload.get('candidate_ids')
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise LedgerError('candidate_ids must be a non-empty list')
    rec = {
        'request_id': _uuid(payload.get('request_id'), 'request_id'),
        'ticker': _ticker(payload.get('ticker')),
        'signal_date': signal_date,
        'candidate_ids': sorted({_uuid(c, 'candidate_ids') for c in candidate_ids}, key=str),
        'brief_id': _uuid(payload.get('brief_id'), 'brief_id', optional=True),
        'proposed_action': _choice(payload.get('proposed_action'), 'proposed_action', ACTIONS),
        'final_action': _choice(payload.get('final_action'), 'final_action', FINAL_ACTIONS),
        'decision': _choice(payload.get('decision'), 'decision', ('accept', 'reject', 'downgrade')),
        'reason': _text(payload.get('reason'), 'reason'),
        'supersedes_id': _uuid(payload.get('supersedes_id'), 'supersedes_id', optional=True),
        'plan_id': _uuid(payload.get('plan_id'), 'plan_id', optional=True),
        'own_plan': _own_plan(payload.get('own_plan')),
        'agent_plan_id': _uuid(payload.get('agent_plan_id'), 'agent_plan_id', optional=True),
        'review_view': _review_view(payload.get('review_view')),
    }
    if rec['plan_id'] is not None and rec['own_plan'] is not None:
        raise LedgerError('plan_id 与 own_plan 只能二选一')
    if rec['decision'] != 'accept' and (rec['plan_id'] is not None or rec['own_plan'] is not None):
        raise LedgerError('否则决与降级不需要计划')
    expected = {'accept': rec['proposed_action'], 'reject': 'ignore', 'downgrade': 'watch'}[rec['decision']]
    if rec['final_action'] != expected or (rec['decision'] == 'downgrade' and rec['proposed_action'] == 'watch'):
        raise LedgerError(f"{rec['decision']} of {rec['proposed_action']} cannot end as {rec['final_action']}")
    return rec



def record_decision(conn, payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """v2.5 §9.3: with an agent-2 chain, ``agent_plan_id`` defaults to the head and any other version is
    AgentPlanStale (409, nothing written). Without migration 026 a request that carries ``agent_plan_id``
    or ``review_view`` is Unavailable (503「迁移 026 未执行」); a plain rule / agent-1 decision still works."""
    rec = validate_decision(payload)
    digest = decision_hash(rec)
    core_dbutil.lock(conn, f"decision:{rec['ticker']}:{rec['signal_date']}")
    ready = core_schema.timeline_ready(conn)
    if not ready and any(rec[k] is not None for k in DECISION_V25_KEYS):
        import investment_assistant.core.tx as _service

        raise _service.Unavailable(MIGRATION_MISSING)
    if replay := _replay(execution_repo_legacy.decision_by_request(conn, rec['request_id'], timeline=ready), digest):
        return replay
    if execution_repo_legacy.count_unit_candidates(conn, rec['ticker'], rec['signal_date'], rec['candidate_ids']) != len(rec['candidate_ids']):
        raise LedgerError('candidate_ids must all belong to this ticker and signal_date')
    if rec['brief_id'] and not execution_repo_legacy.brief_in_unit(conn, rec['brief_id'], rec['ticker'], rec['signal_date']):
        raise LedgerError('brief_id does not belong to this unit')
    head = execution_repo_legacy.decision_head(conn, rec['ticker'], rec['signal_date'])
    if head is None and rec['supersedes_id'] is not None:
        raise LedgerError('nothing to supersede in this unit')
    if head is not None and rec['supersedes_id'] != head['id']:
        raise Conflict('this unit already has a newer decision; supersede the latest one')
    chain_head = execution_repo.chain_head(conn, rec['ticker'], rec['signal_date']) if ready else None
    rec = {**rec, 'agent_plan_id': _checked_agent_plan_id(conn, rec, chain_head)}
    rule = execution_repo.rule_plan(conn, rec['ticker'], rec['signal_date'])
    plan_id = _checked_plan_id(conn, rec, rule, chain_head)
    if rec['own_plan'] is not None:
        plan_id = _record_own_plan(conn, rec, rule, now=now)['id']
    rec = {**rec, 'plan_id': plan_id}
    record = execution_repo_legacy._insert_decision_for_tests(conn, rec, digest, decided_at=now)
    if ready:      # same keys whichever INSERT ran
        record = {**record, 'agent_plan_id': record.get('agent_plan_id'), 'review_view': record.get('review_view')}
    return {'status': 'created', 'record': record}


def _stale_version(conn, rec: dict[str, Any], plan_id, chain_head: dict[str, Any]) -> bool:
    """True when ``plan_id`` is a non-head version of this unit's chain."""
    if plan_id == chain_head['plan']['id']:
        return False
    version = execution_repo.version_by_plan(conn, plan_id)
    return version is not None and version['ticker'] == rec['ticker'] and version['signal_date'] == rec['signal_date']


def _checked_agent_plan_id(conn, rec: dict[str, Any], chain_head: dict[str, Any] | None):
    """v2.5 §9.3 / D15: the Agent version the page showed. With a chain: default the head; an older
    version of this chain → AgentPlanStale; anything else → 400. Without a chain only the unit's own
    (agent-1) agent plan is accepted, and nothing is filled in."""
    given = rec['agent_plan_id']
    if chain_head is not None:
        if given is None or given == chain_head['plan']['id']:
            return chain_head['plan']['id']
        if _stale_version(conn, rec, given, chain_head):
            raise AgentPlanStale(chain_head['version']['version_no'])
        raise LedgerError('agent_plan_id 不属于该决策单元的 Agent 计划')
    if given is None:
        return None
    plan = execution_repo.plan_by_id(conn, given)
    if plan is None or plan['kind'] != 'agent' or plan['ticker'] != rec['ticker'] \
            or plan['signal_date'] != rec['signal_date']:
        raise LedgerError('agent_plan_id 不属于该决策单元的 Agent 计划')
    return given


def _checked_plan_id(conn, rec: dict[str, Any], rule: dict[str, Any] | None,
                     chain_head: dict[str, Any] | None = None):
    """spec §5.3 + P3 §9: an accept of a buy unit carries the unit's ok rule plan,
    its ok agent plan, or own levels — and plan_id may point at nothing else.

    v2.5 §9.3: with an agent-2 chain the agent candidate is the head (and only when ``status = 'ok'``);
    an older version of the chain as plan_id is AgentPlanStale."""
    needs_plan = rec['decision'] == 'accept' and rec['proposed_action'] == 'buy'
    if needs_plan:
        allowed = []
        if rule is not None and rule['status'] == 'ok':
            allowed.append(rule['id'])
        agent = (chain_head['plan'] if chain_head is not None
                 else execution_repo.agent_plan(conn, rec['ticker'], rec['signal_date']))
        if agent is not None and agent['status'] == 'ok':
            allowed.append(agent['id'])
        has_any_plan = rule is not None or agent is not None
        if rec['plan_id'] is None and rec['own_plan'] is None and has_any_plan:
            raise LedgerError('采纳买入必须携带规则计划（plan_id）或自定价位（own_plan）')
        if rec['plan_id'] is not None and rec['plan_id'] not in allowed:
            if chain_head is not None and _stale_version(conn, rec, rec['plan_id'], chain_head):
                raise AgentPlanStale(chain_head['version']['version_no'])
            raise LedgerError('plan_id 必须指向该单元可用的规则计划或 Agent 计划，或改用自定价位')
    if rec['plan_id'] is not None:
        linked = execution_repo.plan_by_id(conn, rec['plan_id'])
        if linked is None or linked['ticker'] != rec['ticker'] or linked['signal_date'] != rec['signal_date']:
            raise LedgerError('plan_id 不属于该决策单元')
    return rec['plan_id']


def _record_own_plan(conn, rec: dict[str, Any], rule: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    """Build and store the own plan in this decision's transaction; replay by request_id is idempotent."""
    from investment_assistant.config import load_config
    import investment_assistant.core.tx as _service
    from investment_assistant.execution import plan_levels as _plans

    bars = data_repo.read_bars(conn, rec['ticker'], rec['signal_date'], cutoff_at=now)
    if not bars:
        raise LedgerError('该单元缺少 K 线，无法生成自定计划')
    plan = _plans.build_own_plan(
        rule_plan=rule, levels={k: rec['own_plan'][k] for k in ('entry_low', 'entry_high', 'stop', 'target1')},
        ticker=rec['ticker'], signal_date=rec['signal_date'], bars=bars,
        earnings_row=data_repo.latest_earnings(conn, rec['ticker'], cutoff_at=now),
        snapshot=execution_repo.snapshot_at(conn, rec['signal_date'], cutoff_at=now),
        exposure=execution_plan_context.exposure(conn, rec['signal_date'], cutoff_at=now, ticker=rec['ticker']),
        params=load_config().workbench,
        context=execution_plan_context.plan_context(conn, rec['ticker'], rec['signal_date'], now=now))
    plan = {**plan, 'request_id': rec['own_plan']['request_id'], 'request_hash': request_hash(plan)}
    existing = execution_repo.plan_by_request(conn, plan['request_id'])
    if existing is not None:
        if existing['request_hash'] != plan['request_hash']:
            raise Conflict('own_plan.request_id 已用于不同内容')
        return existing
    return execution_repo.insert_plan(conn, plan)




def decide(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """What ``execution_trades.decide`` was before v2.9: record a decision in a transaction of its own (old data for the tests)."""
    import investment_assistant.core.tx as service

    with service._tx() as conn:
        return record_decision(conn, payload, now=now)


def add_trade(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """A fill the way it was recorded before v2.9, optionally linked to a decision (``decision_id``): the new
    ``ledger.record_trade`` refuses that link, old data still has it. Everything else is validated as today."""
    import investment_assistant.core.tx as service
    from investment_assistant.execution import ledger

    payload = dict(payload)
    decision_id = _uuid(payload.pop('decision_id', None), 'decision_id', optional=True)
    rec = ledger.validate_trade(payload, now=now)
    digest = request_hash({**{k: v for k, v in rec.items() if k not in ('plan_id', 'own_plan')},
                           'decision_id': decision_id})
    with service._tx() as conn:
        core_dbutil.lock(conn, f"trades:{rec['ticker']}")
        if replay := ledger._replay(execution_repo.trade_by_request(conn, rec['request_id']), digest):
            return replay
        stored = {**{k: v for k, v in rec.items() if k != 'own_plan'}, 'decision_id': decision_id}
        ledger._check_book(conn, rec['ticker'], [*execution_repo.list_trades(conn, rec['ticker']),
                                                  {**stored, 'id': 'new', 'voided_at': None}])
        return {'status': 'created', 'record': execution_repo.insert_trade(conn, stored, digest, recorded_at=now)}
