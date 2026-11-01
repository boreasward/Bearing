import importlib.util
import pytest


def service():
    assert importlib.util.find_spec('investment_assistant.analysis.fundamentals'), 'missing research read API'
    from investment_assistant.analysis import fundamentals as service
    return service


@pytest.mark.parametrize('body', [{}, {'symbols':['MU'], 'datasets':['financials']}, {'symbols': []}, {'symbols':['688008.SH']}, {'symbols':'MU'}, {'datasets':[]}, {'datasets':['other']}, {'retry_failed':'yes'}])
def test_invalid_sync_never_schedules(client, monkeypatch, body):
    from investment_assistant.core import runner
    def forbidden(*a, **kw): pytest.fail('invalid request scheduled work')
    monkeypatch.setattr(runner, 'submit', forbidden)
    response = client.post('/api/us-research/sync', json=body)
    assert response.status_code == 410
    assert response.json() == {'error': 'use_data_menu'}


def test_get_uses_local_view_and_passes_cutoff(client, monkeypatch):
    s = service()
    def view(ticker, *, as_of=None):
        assert (ticker, as_of) == ('MU', '2026-10-01T00:00:00Z')
        return {'security_id':'US:stock:MU','pit':{'strict':False}}
    monkeypatch.setattr(s, 'view', view)
    assert client.get('/api/us-research/MU?as_of=2026-10-01T00:00:00Z').json()['security_id'] == 'US:stock:MU'


def test_retired_sync_has_no_package_task_or_catalog_entry():
    from pathlib import Path
    from investment_assistant.tasks import catalog, scheduler
    assert not Path('investment_assistant/us_research').exists()
    assert importlib.util.find_spec('investment_assistant.tasks.us_research') is None
    assert 'us-research' not in catalog.JOB_INFO
    assert 'us-research' not in scheduler.REGISTRY
