"""Knowledge citation extraction (v2.7 spec §4.4). Pure functions: no database, no network."""
from investment_assistant.core import knowledge


def know_call(seq, *refs, phase='propose', error=None):
    result = {'ref': f'call:{seq}'}
    if error:
        result['error'] = error
    else:
        result['data'] = {'entries': [{'ref': r} for r in refs]}
    return {'seq': seq, 'phase': phase, 'tool': 'get_knowledge', 'args': {}, 'result': result}


def other_call(seq, tool='get_price_bars'):
    return {'seq': seq, 'phase': 'propose', 'tool': tool, 'args': {}, 'result': {'ref': f'call:{seq}', 'data': []}}


def rows(**kwargs):
    return sorted(knowledge.knowledge_citations(**kwargs), key=lambda r: (r['field'], r['knowledge_id'], r['version']))


def test_knowledge_citations_review():
    calls = [know_call(1, 'know:3@2', phase='review')]
    out = rows(output={'outcome': 'maintain', 'reason': 'ok', 'refs': ['know:3@2', 'call:1']}, counter=None,
               mode='review', calls=calls)
    assert out == [{'knowledge_id': 3, 'version': 2, 'field': 'review', 'call_seq': 1}]
    assert rows(output={'outcome': 'maintain', 'reason': 'ok', 'refs': []}, counter=None, mode='review',
                calls=calls) == []


def test_knowledge_citations_ignore_what_no_call_returned():
    calls = [know_call(1, 'know:3@2'), know_call(2, error='我的知识不可用：迁移 027 未执行'), other_call(3)]
    output = {'thesis': [{'text': 'a', 'refs': ['know:9@1', 'know:3', 'know:x@1', 7, 'call:3', 'know:3@2']}]}
    assert rows(output=output, counter=None, mode='propose', calls=calls) == [
        {'knowledge_id': 3, 'version': 2, 'field': 'thesis', 'call_seq': 1}]
    assert rows(output=None, counter=None, mode='propose', calls=calls) == []
    assert rows(output={'thesis': []}, counter={}, mode='propose', calls=[]) == []
