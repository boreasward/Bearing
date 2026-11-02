"""The push node's digest carries the run's portfolio check (v2.14 spec §7.4, K2): the frozen switch decides, a stored
row of an earlier run is not shown when the switch is off, and a missing row sends nothing."""
import uuid

import pytest

import investment_assistant.core.features as core_features
import investment_assistant.execution.repo as execution_repo
from investment_assistant.core.constants import REAL_ACCOUNT_ID
from investment_assistant.core.versions import PORTFOLIO_CHECK_VERSION
from investment_assistant.notify import templates
from investment_assistant.workflow.nodes import push as push_node
from test_workbench_paper_accounts import END, NOW, conn  # noqa: F401

RESULT = {'exposure_by_sector': {}, 'conflicts': [], 'params_used': {}, 'version': PORTFOLIO_CHECK_VERSION,
          'verdicts': [{'plan_id': 'a', 'ticker': 'NVDA', 'group': '半导体', 'verdict': 'ok', 'reasons': [], 'order': 1},
                       {'plan_id': 'b', 'ticker': 'AMD', 'group': '半导体', 'verdict': 'blocked',
                        'reasons': ['sector_cap'], 'order': None}],
          'order': ['a']}


class Cfg:
    channel = 'discord'
    discord_enabled = True
    task_enabled = {'workbench': True}


def pushed(**kwargs):
    sent = []

    def send(kind, payload, notify_cfg, client=None):
        sent.append(payload)
        return {'sent': True, 'messages': len(templates.workbench_digest(payload))}
    out = push_node.push(now=NOW, force=True, summary={'errors': [], 'earnings': {}}, notify_cfg=Cfg(), send=send,
                         **kwargs)
    assert out['sent'] is True
    return sent[0]


def store(conn):
    execution_repo.insert_portfolio_check(conn, {'account_id': REAL_ACCOUNT_ID, 'session': END, 'run_id': 'r1',
        'cutoff_at': NOW, 'version': PORTFOLIO_CHECK_VERSION, 'result': RESULT})
    conn.commit()


def test_digest_carries_the_check_of_the_session(conn):
    store(conn)
    payload = pushed(features={'portfolio_manager': True})
    assert payload['portfolio'] is not None
    assert (payload['portfolio']['ok'], payload['portfolio']['blocked'], payload['portfolio']['order']) == (1, 1, ['NVDA'])
    assert payload['portfolio']['status'] == 'degraded'                   # a hard result without commentary
    assert any(m.title.startswith('🧩 组合检查') for m in templates.workbench_digest(payload))


def test_switch_off_in_the_run_sends_no_check_even_with_a_stored_row(conn):
    store(conn)
    assert pushed(features={'portfolio_manager': False})['portfolio'] is None


def test_frozen_switch_beats_a_flip_during_the_run(conn):
    store(conn)
    core_features.record_change(conn, flag='portfolio_manager', enabled=False, reason='mid-run', request_id=uuid.uuid4())
    conn.commit()
    assert pushed(features={'portfolio_manager': True})['portfolio'] is not None     # the run froze it on
    assert pushed()['portfolio'] is None                                              # no frozen set: the current switch


def test_no_row_sends_no_portfolio_message(conn):
    assert pushed(features={'portfolio_manager': True})['portfolio'] is None
