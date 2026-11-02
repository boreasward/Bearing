"""The four new command-line entries (spec §12.3, R12a): ``data.registry list / fetch``, ``derivation.indicators
list / compute``, ``analysis.research_requests run / show`` and ``optimize.roadmap import / export`` (its own tests are
in test_api_roadmap). Disposable schema, synthetic bars, no network."""
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.core.tx as core_tx
import investment_assistant.data.collect as collect
import investment_assistant.data.repo as data_repo
import investment_assistant.views.data as data_view
from investment_assistant.analysis import research_requests
from investment_assistant.data import registry
from investment_assistant.derivation.indicators import __main__ as indicators_cli
from investment_assistant.derivation.indicators import registry as indicator_registry
from test_workbench_data_fetch import NOW, SESSION, Sources, db, deps  # noqa: F401  (db fixture)

ROOT = Path(__file__).resolve().parent.parent
MODULES = ('investment_assistant.data.registry', 'investment_assistant.derivation.indicators',
           'investment_assistant.analysis.research_requests', 'investment_assistant.optimize.roadmap')


@pytest.mark.parametrize('module', MODULES)
def test_module_entry_prints_help_without_a_database(module):
    done = subprocess.run([sys.executable, '-m', module, '--help'], capture_output=True, text=True, cwd=ROOT,
                          env={'PATH': '', 'PYTHON_DOTENV_DISABLED': '1'})
    assert done.returncode == 0 and 'usage' in done.stdout and module in done.stdout, done.stderr


def test_no_package_level_second_entry():
    for package in ('data', 'optimize'):
        assert not (ROOT / 'investment_assistant' / package / '__main__.py').exists()


# ---------------------------------------------------------------- data.registry

def test_registry_list_has_every_family(capsys):
    assert registry.main(['list']) == 0
    out = capsys.readouterr().out
    assert [line.split()[0] for line in out.splitlines()[1:]] == list(registry.ids())


def test_registry_fetch_argument_errors(db, capsys):
    assert registry.main(['fetch', 'nope']) == 2                          # unknown family
    assert registry.main(['fetch', 'bars']) == 2                          # a per-ticker family needs --ticker
    assert registry.main(['fetch', 'fred', '--ticker', 'NVDA']) == 2      # a whole-family one takes none
    assert registry.main(['fetch', 'bars', '--ticker', 'not a ticker']) == 2
    err = capsys.readouterr().err
    assert '未知数据族' in err and '--ticker' in err and '非法 ticker' in err
    assert db.execute('SELECT count(*) FROM data_fetch_runs').fetchone()[0] == 0


def test_registry_fetch_one_ticker_writes_a_manual_family_row(db, monkeypatch, capsys):
    fake = deps(Sources())
    monkeypatch.setattr(core_tx, 'Deps', lambda: fake)
    monkeypatch.setattr(collect, 'wall_clock', lambda: NOW)
    assert registry.main(['fetch', 'bars', '--ticker', 'mu']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['status'] == 'success' and printed['ticker'] == 'MU' and printed['ok'] == 1
    assert db.execute("SELECT count(*) FROM price_bars WHERE ticker = 'MU'").fetchone()[0] > 0
    assert db.execute('SELECT family, job, trigger, scope, ticker, session, status FROM data_fetch_runs').fetchall() \
        == [('bars', 'workbench', 'manual_family', 'ticker', 'MU', SESSION, 'success')]


# ---------------------------------------------------------------- derivation.indicators

def test_indicators_list_has_every_indicator(capsys):
    assert indicators_cli.main(['list']) == 0
    out = capsys.readouterr().out
    assert [line.split()[0] for line in out.splitlines()[1:]] == [i.id for i in indicator_registry.INDICATORS]


def test_indicators_compute_matches_the_registry(db):
    data_view.run_fetch('bars', 'MU', run_id='seed', now=NOW, deps=deps(Sources()))
    data_view.run_fetch('bars', 'SPY', run_id='seed-spy', now=NOW, deps=deps(Sources()))
    db.commit()
    bars = data_repo.read_bars(db, 'MU', SESSION, cutoff_at=NOW)
    spy = data_repo.read_bars(db, 'SPY', SESSION, cutoff_at=NOW)
    for indicator_id, benchmark in (('atr14', None), ('ma50_adj_close', None), ('rs20', spy)):
        found = indicators_cli.compute_one(indicator_id, ' mu ', now=NOW)
        expected = indicator_registry.compute(indicator_id, bars, session=SESSION, benchmark_bars=benchmark)
        assert found['value'] == expected and found['value'] is not None, indicator_id
        assert found['ticker'] == 'MU' and found['session'] == SESSION and found['computed_through'] == SESSION


def test_indicators_compute_refuses_what_it_cannot_do_honestly(db, capsys):
    for args in (['breadth', 'NVDA'], ['sector_strength', 'NVDA'], ['nope', 'NVDA'], ['atr14', 'not a ticker']):
        assert indicators_cli.main(['compute', *args]) == 2
    err = capsys.readouterr().err
    assert 'indicator_requires_universe' in err and 'indicator_requires_sector_etf' in err and '未知指标' in err
    found = indicators_cli.compute_one('atr14', 'NVDA', now=NOW)            # no stored bars: insufficient history
    assert found['value'] is None and found['computed_through'] is None


# ---------------------------------------------------------------- analysis.research_requests

def test_research_requests_show_and_run_errors(db, capsys):
    request = analysis_repo.insert_research_request(db, {
        'id': str(uuid.uuid4()), 'ticker': 'NVDA', 'question': '护城河？', 'horizon_days': 30,
        'request_id': str(uuid.uuid4()), 'created_at': datetime(2026, 10, 23, tzinfo=UTC)})
    db.commit()
    assert research_requests.main(['show', str(request['id'])]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['request']['ticker'] == 'NVDA' and shown['versions'] == [] and shown['chain'] is None
    assert research_requests.main(['show', str(uuid.uuid4())]) == 2
    assert research_requests.main(['run', str(uuid.uuid4())]) == 2
    err = capsys.readouterr().err
    assert '没有这个研究请求' in err and '没有待处理的研究请求' in err
