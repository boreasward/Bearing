"""Fix round 1: what the hosts hand the agent tools (R15 breadth sample, §6.2 filing-text root) and the research
segment a rejected first version keeps (I3, D42/R16)."""
import hashlib
import json
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from _daily_db import daily_db as _daily_wired  # noqa: F401  (registers the fixture)
from _daily_db import job_reports  # noqa: F401  (registers the fixture)
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis import units
from investment_assistant.analysis.agent import runner, tools
from investment_assistant.core import tx as core_tx
from investment_assistant.core.llm.model import AgentSettings
from investment_assistant.data import repo as data_repo
from investment_assistant.db import upsert_watchlist_item
from investment_assistant.workflow import state as workflow_state

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
SESSION = date(2026, 10, 30)
SETTINGS = AgentSettings(provider='chatgpt', model='gpt-test', api_key='sk-test', daily_tokens=None, config_error=None)


@pytest.fixture
def wired_db(request):
    return request.getfixturevalue('_daily_wired')


def series(trend: str, n: int = 60, session: date = SESSION) -> list[dict]:
    """``n`` consecutive daily bars ending on ``session``; ``up`` closes above its 50-bar mean, ``down`` below."""
    step = 1 if trend == 'up' else -1
    rows = []
    for i in range(n):
        close = 100 + step * i
        rows.append({'price_date': session - timedelta(days=n - 1 - i), 'open': close, 'high': close + 1,
                     'low': close - 1, 'close': close, 'adj_close': close, 'volume': 1000})
    return rows


def ctx_for(ticker='AMD', **kwargs):
    return tools.ToolContext(ticker, SESSION, NOW, lambda fn: fn(None), **kwargs)


# ---------------------------------------------------------------- R15: get_indicator('breadth') / sector_strength

def test_breadth_over_a_multi_ticker_sample_with_opposed_trends(monkeypatch):
    trends = {'UPA': 'up', 'UPB': 'up', 'DOWN': 'down'}
    monkeypatch.setattr(data_repo, 'read_bars', lambda conn, symbol, session, **kw: series(trends[symbol]))
    result = tools.get_indicator(ctx_for('UPA', breadth_tickers=('UPA', 'UPB', 'DOWN')), 'UPA', 'breadth')
    value = result['value']
    assert (value['tickers'], value['eligible_50'], value['above_50']) == (3, 3, 2)
    assert value['pct_50'] == pytest.approx(2 / 3)
    assert result['computed_through'] == SESSION.isoformat()
    # one ticker is not a pool: the same indicator over a single member gives a different, single-member answer
    single = tools.get_indicator(ctx_for('UPA', breadth_tickers=('UPA',)), 'UPA', 'breadth')['value']
    assert single['tickers'] == 1 and single['pct_50'] == 1.0


def test_breadth_reads_every_member_at_the_cutoff(monkeypatch):
    seen = []

    def read(conn, symbol, session, *, cutoff_at, **kwargs):
        seen.append((symbol, session, cutoff_at))
        return series('up')

    monkeypatch.setattr(data_repo, 'read_bars', read)
    tools.get_indicator(ctx_for('AMD', breadth_tickers=('AMD', 'NVDA', 'AAPL')), 'AMD', 'breadth')
    assert sorted(symbol for symbol, _, _ in seen) == ['AAPL', 'AMD', 'NVDA']
    assert all(session == SESSION and cutoff == NOW for _, session, cutoff in seen)


def test_breadth_cutoff_excludes_bars_first_seen_later(wired_db):
    """The real read path: a member whose session bar was recorded after the cutoff does not count."""
    rows = {'EARLY': series('up'), 'LATE': series('up')}
    for symbol, bars in rows.items():
        seen_at = NOW - timedelta(hours=1) if symbol == 'EARLY' else NOW + timedelta(hours=1)
        for bar in bars:
            wired_db.execute(
                'INSERT INTO price_bars (ticker, price_date, open, high, low, close, adj_close, volume, source, '
                "revision, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'yfinance',1,%s)",
                (symbol, bar['price_date'], bar['open'], bar['high'], bar['low'], bar['close'], bar['adj_close'],
                 bar['volume'], seen_at))
    wired_db.commit()
    ctx = tools.ToolContext('EARLY', SESSION, NOW, lambda fn: fn(wired_db), breadth_tickers=('EARLY', 'LATE'))
    value = tools.get_indicator(ctx, 'EARLY', 'breadth')['value']
    assert value['tickers'] == 1                         # LATE's bars were not known at the cutoff


def test_breadth_without_a_sample_is_unknown_not_empty(monkeypatch):
    monkeypatch.setattr(data_repo, 'read_bars', lambda *a, **kw: series('up'))
    with pytest.raises(tools.ToolError, match='indicator_requires_universe'):
        tools.get_indicator(ctx_for(), 'AMD', 'breadth')                      # no sample supplied
    empty = tools.get_indicator(ctx_for(breadth_tickers=()), 'AMD', 'breadth')    # a known, empty sample
    assert empty['value']['tickers'] == 0 and empty['value']['pct_50'] is None


def test_sector_strength_for_an_etf_uses_spy(monkeypatch):
    rises = {'XLK': series('up', 70), 'SPY': series('up', 70)}
    for row in rises['SPY']:                                   # SPY flat: the ETF outperforms it
        row['adj_close'] = 100
    monkeypatch.setattr(data_repo, 'read_bars', lambda conn, symbol, session, **kw: rises[symbol])
    result = tools.get_indicator(ctx_for('XLK'), 'XLK', 'sector_strength')
    assert result['value'] is not None and result['value']['rs20'] > 0 and result['value']['rs60'] > 0
    with pytest.raises(tools.ToolError, match='indicator_requires_sector_etf'):
        tools.get_indicator(ctx_for('SPY'), 'SPY', 'sector_strength')


# ---------------------------------------------------------------- hosts

def test_host_breadth_sample_follows_the_regime_policy(wired_db):
    for ticker in ('AMD', 'NVDA'):
        upsert_watchlist_item(wired_db, ticker=ticker)
    wired_db.commit()
    deps = core_tx.Deps(universe_loader=lambda: SimpleNamespace(tickers=('AAPL', 'AMD', 'MSFT')))
    assert units.host_breadth_sample(deps) == ('AAPL', 'AMD', 'MSFT', 'NVDA')       # universe ∪ manual pool, deduped
    # an unreadable universe is not an error the host hides: the regime policy falls back to the manual pool
    from investment_assistant.data.adapters import universe as universe_module

    def broken():
        raise universe_module.UniverseError('cannot read')

    assert units.host_breadth_sample(core_tx.Deps(universe_loader=broken)) == ('AMD', 'NVDA')


def test_state_sample_is_the_regime_nodes_sample():
    pool = {'core': ['AMD'], 'extended': ['NVDA'], 'sectors': {}, 'sources': {}}
    state = {'pool': pool, 'manual_pool': pool, 'universe': {'tickers': ['AAPL', 'AMD']}}
    ctx = SimpleNamespace(breadth_tickers=None)
    assert workflow_state.breadth_sample(state, ctx) == ('AAPL', 'AMD', 'NVDA')
    assert workflow_state.breadth_sample({**state, 'explicit': True}, ctx) == ('AMD', 'NVDA')
    assert workflow_state.breadth_sample(state, SimpleNamespace(breadth_tickers=['ZZZ'])) == ('ZZZ',)
    assert workflow_state.breadth_sample({'summary': {}}, ctx) is None            # no pool in the state: host resolves


# ---------------------------------------------------------------- end to end through run_agent_unit

def tool(name, args):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': name}])


def test_run_agent_unit_gives_the_researcher_filing_text_and_the_proposer_breadth(
        wired_db, monkeypatch, scripted_model, tmp_path):
    """I1/I2/I3 through the real host: the batch researcher reads an original filing (``sec_text_dir`` from the
    configured filings root), the proposer's ``get_indicator('breadth')`` answers over the host's frozen sample, and the
    first version — rejected by the plan check — still stores the researcher's segment (D42/R16)."""
    wired_db.execute(Path('migrations/014_us_research.sql').read_text(encoding='utf-8'))   # the observation store
    accession = '0001234567-26-000001'
    folder = tmp_path / 'AMD'
    folder.mkdir()
    (folder / f'{accession}.txt').write_text('前言\nItem 1A. Risk Factors\n竞争加剧的风险。\nItem 7. MD&A\n稳定。\n')
    filing = wired_db.execute(
        'INSERT INTO filings (ticker, form, filing_date, accession_number, primary_document, file_path, downloaded_at) '
        "VALUES ('AMD','10-K',%s,%s,'a.htm',%s,%s) RETURNING id",
        (SESSION - timedelta(days=30), accession, str(folder / f'{accession}.txt'), NOW - timedelta(days=1))).fetchone()[0]
    for ticker in ('AMD', 'NVDA'):
        upsert_watchlist_item(wired_db, ticker=ticker)
    wired_db.commit()
    trends = {'AMD': 'up', 'NVDA': 'down', 'AAPL': 'up'}
    monkeypatch.setattr(data_repo, 'read_bars', lambda conn, symbol, session, **kw: series(trends.get(symbol, 'up')))
    monkeypatch.setattr('investment_assistant.config.load_config',
                        lambda *a, **kw: SimpleNamespace(filings=SimpleNamespace(output_dir=tmp_path)))
    research = json.dumps({'facts': [{'text': '风险披露见 Item 1A', 'kind': 'disclosure', 'refs': ['call:1']}],
                           'risks': [], 'blocked': [], 'summary': '已读原文'}, ensure_ascii=False)
    model = scripted_model([tool('get_filing_text', {'filing_id': filing, 'section': 'item_1a'}),
                            AIMessage(content=research),
                            tool('get_indicator', {'ticker': 'AMD', 'indicator_id': 'breadth'}),
                            AIMessage(content='不是 JSON')])
    deps = core_tx.Deps(agent_model=model, agent_settings=SETTINGS,
                        agent_checkpointer=lambda: nullcontext(InMemorySaver()),
                        universe_loader=lambda: SimpleNamespace(tickers=('AAPL',)))
    unit = {'ticker': 'AMD', 'signal_date': SESSION, 'pool': 'extended', 'pool_tier': 'extended',
            'proposed_action': 'buy', 'labels': [], 'candidates': []}
    run = runner.run_agent_unit(unit, now=NOW, deps=deps, settings=SETTINGS, attempt=1, run_id='daily-r1',
                                features={'company_researcher': True})
    assert run['status'] == 'rejected', (run['reason'], run['detail'])
    calls = {call['tool']: call for call in analysis_repo.agent_tool_calls(wired_db, run['id'])}
    text = calls['get_filing_text']['result']
    assert 'error' not in text and '竞争加剧的风险' in text['data']['text']        # I2: the original text is readable
    assert text['data']['sha256'] == hashlib.sha256((folder / f'{accession}.txt').read_bytes()).hexdigest()
    breadth = calls['get_indicator']['result']
    assert 'error' not in breadth, breadth                                          # I1: the sample was supplied
    assert (breadth['data']['value']['tickers'], breadth['data']['value']['above_50']) == (3, 2)   # AAPL ∪ AMD ∪ NVDA
    stored = data_repo.agent_run_by_id(wired_db, run['id'])
    assert stored is not None and stored['status'] == 'rejected'
    assert stored['output']['research']['status'] == 'ok'                           # I3: kept although the plan was not
    assert stored['output']['research']['output']['summary'] == '已读原文'


def test_unopened_rerun_carries_the_segment_of_a_rejected_first_version(wired_db, monkeypatch):
    """I3 → D42: the data-request rerun of an unopened unit finds the rejected run's research segment and carries it
    (R16: the original run stays the owner), and it looks past a later error row that has none."""
    segment = {'status': 'ok', 'reason': None, 'output': {'facts': [], 'risks': [], 'blocked': [], 'summary': 's'}}
    first = {'id': 'a' * 8 + '-0000-4000-8000-' + '0' * 12, 'ticker': 'AMD', 'signal_date': SESSION, 'pool': 'main',
             'attempt': 1, 'mode': 'propose', 'agent_version': 'agent-4', 'provider': 'chatgpt', 'model': 'm',
             'cutoff_at': NOW, 'status': 'rejected', 'reason': 'invalid_citation', 'detail': None,
             'output': {'research': segment}, 'counter': None, 'plan_id': None, 'input_tokens': 0,
             'output_tokens': 0, 'started_at': NOW, 'finished_at': NOW}
    second = {**first, 'id': 'b' * 8 + '-0000-4000-8000-' + '0' * 12, 'attempt': 2, 'status': 'error',
              'reason': 'model_error', 'output': None}
    analysis_repo.insert_agent_run(wired_db, first, [])
    analysis_repo.insert_agent_run(wired_db, second, [], data_requests=[
        {'family': 'fundamentals', 'ticker': 'AMD', 'run_id': 'daily-r1'}])
    wired_db.commit()
    from test_agent_request_data import _fake_adapter_run

    _fake_adapter_run(monkeypatch, rows=2)
    seen = {}
    monkeypatch.setattr(runner, '_unit_for_request_rerun',
                        lambda conn, ticker, signal_date, *, prior, now:
                        {'ticker': ticker, 'signal_date': signal_date, 'pool': 'main', 'pool_tier': 'core'})

    def fake_propose(unit, *, now, deps, settings, attempt, run_id, features=None, carried_research=None,
                     previous_run_id=None, breadth_tickers=None):
        seen.update(carried=carried_research, owner=previous_run_id, features=features)
        return {'id': 'c' * 8 + '-0000-4000-8000-' + '0' * 12}

    monkeypatch.setattr(runner, 'run_agent_unit', fake_propose)
    counts = runner.process_data_requests(SESSION, run_id='daily-r1', now=NOW,
                                          deps=core_tx.Deps(agent_settings=SETTINGS, clock=lambda: 0.0),
                                          features={'company_researcher': True}, budget_left_s=600)
    assert counts['reruns'] == 1
    assert seen['carried'] == segment and seen['owner'] == first['id']             # the original owner, not the error row
    assert seen['features']['company_researcher'] is True                          # carried, not researcher-disabled
