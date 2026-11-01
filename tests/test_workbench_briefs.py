"""Brief rows derived from agent runs (P4 design §7.1): pure functions, no model."""
import uuid
from datetime import UTC, date, datetime

from investment_assistant.analysis import briefs
from investment_assistant.analysis.agent import REASON_TEXT

T = date(2026, 10, 22)
CUTOFF = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)
CID = uuid.UUID('00000000-0000-0000-0000-000000000001')

UNIT = {'ticker': 'NVDA', 'signal_date': T, 'proposed_action': 'buy', 'labels': [], 'candidate_ids': [CID]}
CANDS = [{'id': CID, 'rule_id': 'trend_reclaim', 'action': 'buy', 'inputs': {'values': {'close': 101.0}}}]


# --- for_run / rule_summary (P4 design §7.1) ---

from investment_assistant.analysis.agent import REASON_TEXT


def _run(**overrides):
    values = {'id': uuid.uuid4(), 'ticker': 'NVDA', 'signal_date': T, 'mode': 'propose', 'status': 'ok',
              'reason': None, 'agent_version': 'agent-1', 'model': 'gpt-test', 'cutoff_at': CUTOFF,
              'output': {'action': 'buy', 'summary': '回踩修复后放量上行',
                         'thesis': [{'text': '放量突破 20 日线', 'refs': ['call:1']}]},
              'counter': {'status': 'ok', 'risks': [{'text': '财报临近', 'refs': ['call:2']}], 'dropped': 0}}
    values.update(overrides)
    return values


def _calls(*seqs):
    return [{'seq': seq, 'phase': 'propose', 'tool': 'get_trigger', 'args': {}, 'result': {},
             'result_sha256': 'a' * 64} for seq in seqs]


def test_proposal_ok_brief_from_thesis_and_counter():
    brief = briefs.for_run(_run(), _calls(1, 2), unit=UNIT, candidates=CANDS)
    assert brief['status'] == 'ok' and brief['error'] is None
    assert brief['prompt_version'] == 'agent-1' and brief['model'] == 'gpt-test'
    assert brief['agent_run_id'] == brief['agent_run_id']
    assert brief['cutoff_at'] == CUTOFF
    assert brief['output'] == {'summary': '回踩修复后放量上行',
                               'support': [{'text': '放量突破 20 日线', 'refs': ['call:1']}],
                               'contra': [{'text': '财报临近', 'refs': ['call:2']}],
                               'conflicts': []}


def test_proposal_pass_brief():
    brief = briefs.for_run(_run(status='pass',
                                output={'action': 'pass', 'summary': '趋势不明',
                                        'thesis': [{'text': '量能不足', 'refs': ['call:1']}]},
                                counter={'status': 'malformed', 'risks': [], 'dropped': 0}),
                          _calls(1), unit=UNIT, candidates=CANDS)
    assert brief['status'] == 'ok'
    assert brief['output']['contra'] == []          # 反方 malformed 时为空
    brief_none = briefs.for_run(_run(status='pass', counter=None), _calls(1), unit=UNIT, candidates=CANDS)
    assert brief_none['output']['contra'] == []


def test_interpret_ok_brief():
    run = _run(mode='interpret', agent_version='agent-brief-1',
               output={'summary': '趋势完整', 'support': [{'text': '放量', 'refs': ['call:1']}],
                       'contra': [], 'conflicts': []}, counter=None)
    brief = briefs.for_run(run, _calls(1), unit=UNIT, candidates=CANDS)
    assert brief['status'] == 'ok' and brief['prompt_version'] == 'agent-brief-1'
    assert brief['output'] == run['output']


def test_interpret_rejected_is_error_and_hidden():
    run = _run(mode='interpret', status='rejected', reason='invalid_citation', output=None, counter=None)
    brief = briefs.for_run(run, _calls(1), unit=UNIT, candidates=CANDS)
    assert brief['status'] == 'error' and brief['output'] == {}
    assert brief['error'] == REASON_TEXT['invalid_citation'] == '引用无效已作废'


def test_skipped_run_falls_back_to_rule_summary():
    run = _run(mode='interpret', status='skipped', reason='disabled', output=None, counter=None)
    brief = briefs.for_run(run, [], unit=UNIT, candidates=CANDS)
    assert brief['status'] == 'fallback' and brief['error'] == '已关闭'
    assert brief['output']['summary'].startswith('规则候选：')
    assert briefs.rule_summary(UNIT, CANDS) == brief['output']


def test_input_refs_sorted_by_seq():
    brief = briefs.for_run(_run(), _calls(10, 9), unit=UNIT, candidates=CANDS)
    assert brief['input_refs'] == ['call:9', 'call:10']
