import copy
import json
from datetime import date

import pytest

from investment_assistant.optimize.evidence import EVIDENCE_KEYS, fact_blocks, is_proposal_day, summarize


def inputs():
    return {'fetch_failures': [
                {'family': 'bars', 'error_kind': 'network', 'failed_targets': 2, 'backup_used': 1,
                 'partial': 1, 'failed': 0},
                {'family': 'bars', 'error_kind': 'network', 'failed_targets': 3, 'backup_used': 2,
                 'partial': 0, 'failed': 1}],
            'agent_reasons': [{'mode': 'propose', 'reason': 'invalid_citation', 'count': 2},
                              {'mode': 'propose', 'reason': 'invalid_citation', 'count': 1},
                              {'mode': 'data_request', 'reason': 'deferred', 'count': 4}],
            'research_blocked': [{'why': 'not_found', 'count': 2}, {'why': 'not_found', 'count': 1}],
            'counter_adoption': [{'adopted': 1, 'total': 2}, {'adopted': 1, 'total': 4}],
            'lesson_repeats': [{'scope': '科技', 'count': 1}, {'scope': '科技', 'count': 2},
                               {'scope': '金融', 'count': 1}],
            'stale_versions': [{'version_id': 'v2.11', 'status': 'user_acceptance_pending', 'days_pending': 31},
                               {'version_id': 'v2.10', 'status': 'user_acceptance_pending', 'days_pending': 30},
                               {'version_id': 'v2.9', 'status': 'done', 'days_pending': 90}],
            'open_proposals': [{'id': 42, 'title': '补齐数据', 'status': 'proposed'}]}


def test_summarize_assigns_ids_and_keys():
    source = inputs()
    before = copy.deepcopy(source)
    pack = summarize(**source)
    assert tuple(pack) == ('data_failures', 'agent_quality', 'retro_patterns', 'route', 'open_proposals')
    assert tuple(pack) == EVIDENCE_KEYS
    assert all(isinstance(row['id'], str) and row['id'] for rows in pack.values() for row in rows)
    assert len(pack['data_failures']) == 1
    assert {k: v for k, v in pack['data_failures'][0].items() if k != 'id'} == {
        'family': 'bars', 'error_kind': 'network', 'failed_targets': 5, 'backup_used': 3, 'partial': 1, 'failed': 1, 'backup_incomplete_runs': 0}
    quality = {row['kind'] + ':' + row.get('reason', ''): row for row in pack['agent_quality']}
    assert quality['agent_reason:invalid_citation']['count'] == 3
    assert quality['agent_reason:deferred']['count'] == 4
    assert quality['research_blocked:not_found']['count'] == 3
    assert quality['counter_adoption:']['adopted'] == 2
    assert quality['counter_adoption:']['total'] == 6
    assert abs(quality['counter_adoption:']['ratio'] - 1 / 3) < 1e-12
    assert [(r['scope'], r['count']) for r in pack['retro_patterns']] == [('科技', 3)]
    assert [r['version_id'] for r in pack['route']] == ['v2.11']
    assert pack['open_proposals'][0]['id'] == '42'
    assert source == before


def test_row_ids_survive_reordering_new_groups_and_changing_counts():
    original = summarize(**inputs())
    source = {key: list(reversed(value)) for key, value in inputs().items()}
    assert summarize(**source) == original
    source['fetch_failures'].append({'family': 'news', 'error_kind': 'timeout', 'failed_targets': 9})
    source['fetch_failures'][0]['failed_targets'] += 10
    new_rows = summarize(**source)['data_failures']
    assert next(r for r in new_rows if r['family'] == 'bars')['id'] == original['data_failures'][0]['id']
    assert len({r['id'] for r in new_rows}) == 2


def test_fact_blocks_keep_every_row_id_and_explicit_empty_sections():
    pack = summarize(**inputs())
    blocks = fact_blocks(pack)
    positions = [blocks.index(f'[fact:{key}]') for key in EVIDENCE_KEYS]
    assert positions == sorted(positions)
    for rows in pack.values():
        for row in rows:
            assert row['id'] in blocks
    empty = summarize(**dict.fromkeys(inputs(), []))
    assert all(rows == [] for rows in empty.values())
    for chunk in fact_blocks(empty).split('\n\n'):
        assert json.loads(chunk.split('\n', 1)[1]) == {'status': 'empty', 'rows': []}


def test_zero_denominator_is_unknown_not_zero_adoption():
    source = dict.fromkeys(inputs(), [])
    source['counter_adoption'] = [{'adopted': 0, 'total': 0}]
    row = summarize(**source)['agent_quality'][0]
    assert row['ratio'] is None
    assert row['total'] == 0


@pytest.mark.parametrize('counts,adopted,total,ratio', [
    ([{'adopted': 1, 'total': 2}, {'adopted': 1, 'total': 4}], 2, 6, 1 / 3),
    ([{'adopted': 0, 'total': 0}], 0, 0, None),
])
def test_counter_fact_text_explains_run_level_observation_without_claiming_adoption(counts, adopted, total, ratio):
    source = dict.fromkeys(inputs(), [])
    source['counter_adoption'] = counts
    pack = summarize(**source)
    # Exercise the exact text sent to the model, not a module-level label constant.
    model_text = fact_blocks(pack)
    quality_text = model_text.split('[fact:agent_quality]\n', 1)[1].split('\n\n', 1)[0]
    assert 'counter_followed_by_successful_revision' in quality_text
    assert '运行级后续修订比例，不证明逐条反方意见被采纳或因果关系' in quality_text
    row = json.loads(quality_text)['rows'][0]
    assert row == {
        # Existing persisted refs must still resolve after adding metadata.
        'id': 'af0a89bc2837a93427c53898', 'kind': 'counter_adoption',
        'adopted': adopted, 'total': total, 'ratio': ratio,
        'measurement': 'counter_followed_by_successful_revision',
        'note': '运行级后续修订比例，不证明逐条反方意见被采纳或因果关系',
    }
    assert row == pack['agent_quality'][0]


def test_route_preserves_host_computed_three_month_review_signal():
    source = dict.fromkeys(inputs(), [])
    source['stale_versions'] = [{'kind': 'process_review', 'metric': '执行偏差',
                                 'months': ['2026-08', '2026-09', '2026-10'], 'triggered': True}]
    row = summarize(**source)['route'][0]
    assert row['triggered'] is True
    assert row['months'] == ['2026-08', '2026-09', '2026-10']


def test_is_proposal_day():
    assert is_proposal_day(date(2026, 11, 6)) is True
    assert is_proposal_day(date(2026, 11, 5)) is False
    assert is_proposal_day(date(2026, 11, 7)) is False
    assert is_proposal_day(date(2026, 7, 10)) is True


def test_route_database_id_cannot_override_stable_evidence_id():
    source = inputs()
    first = summarize(**source)['route'][0]['id']
    source['stale_versions'][0]['id'] = 987
    row = summarize(**source)['route'][0]
    assert row['id'] == first
    assert row['source_id'] == 987


@pytest.mark.parametrize('section', ['open_proposals', 'stale_versions'])
def test_ambiguous_source_rows_cannot_make_duplicate_reference_ids(section):
    source = inputs()
    source[section].append(copy.deepcopy(source[section][0]))
    with pytest.raises(ValueError, match='evidence_duplicate_id'):
        summarize(**source)


@pytest.mark.parametrize('bad_count', [-1, 1.5, True, '2'])
def test_invalid_counts_cannot_become_factual_totals(bad_count):
    source = inputs()
    source['fetch_failures'][0]['failed_targets'] = bad_count
    with pytest.raises(ValueError, match='evidence_count_invalid'):
        summarize(**source)


@pytest.mark.parametrize('adoption', [{'adopted': 2, 'total': 1}, {'adopted': 1, 'total': 0},
                                    {'adopted': -1, 'total': 2}])
def test_impossible_adoption_ratio_is_rejected(adoption):
    source = inputs()
    source['counter_adoption'] = [adoption]
    with pytest.raises(ValueError, match='evidence_count_invalid'):
        summarize(**source)


def test_backup_lower_bound_note_and_stable_reference():
    source = dict.fromkeys(inputs(), [])
    source['fetch_failures'] = [{'family': 'bars', 'error_kind': None, 'backup_used': 0,
                                 'backup_incomplete_runs': 0}]
    exact = summarize(**source)['data_failures'][0]
    source['fetch_failures'][0]['backup_incomplete_runs'] = 2
    pack = summarize(**source)
    row = pack['data_failures'][0]
    assert row['backup_used'] == 0 and row['backup_incomplete_runs'] == 2
    assert row['id'] == exact['id']
    assert '下界' in row['note'] and '不完整' in row['note']
    assert row['note'] in fact_blocks(pack)
    assert 'note' not in exact
