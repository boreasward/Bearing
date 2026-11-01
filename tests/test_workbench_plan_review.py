"""Daily review stage `review` (v2.5 spec §7) end to end against a disposable schema.

Fake fetchers serve a fabricated price path (tickers SYN*, every price is synthetic test data); a routed
scripted model answers by prompt (first-version proposal, counter, review, revision), so interpretation runs
for unrelated watch units never eat a reply meant for the review. `now` advances session by session.
"""
from investment_assistant.execution import chains as execution_chains
from investment_assistant.execution import trades as execution_trades
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import json
import uuid

import legacy_decisions
from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from test_workbench_service import db   # noqa: F401  (fixture: service wired to the disposable schema)
from investment_assistant.data.calendar import add_trading_days
import investment_assistant.workflow.nodes.review as plan_review
import investment_assistant.core.tx as service
import investment_assistant.analysis.agent.runner as d_analysis_agent_runner
import investment_assistant.workflow.batch as d_workflow_batch
from investment_assistant.analysis import pool as pool_module
from investment_assistant.analysis import regime as regime_module
import investment_assistant.workflow.recorder as stages_module
from investment_assistant.analysis.agent import graph as agent_graph
from investment_assistant.core.llm import model as agent_model_module
from investment_assistant.data.adapters.universe import Universe

NY = ZoneInfo('America/New_York')
S1 = date(2026, 9, 1)                    # signal session (Tuesday)
HISTORY = 299
FAR = (S1 + timedelta(days=40)).isoformat()
QUIET = [{'kind': 'date_reached', 'params': {'date': FAR}}]      # review condition far away: host maintains


def day(k: int) -> date:
    return add_trading_days(S1, k)


def evening(d: date) -> datetime:
    return datetime.combine(d, time(18, 30), tzinfo=NY).astimezone(UTC)


def _base_path() -> dict[date, float]:
    """Uptrend, 3-day dip, reclaim on S1 (trend_reclaim buy), then +0.5 per session: no new buy after S1."""
    days = [add_trading_days(S1, -(HISTORY - i)) for i in range(HISTORY + 1)]
    closes = [50 + i * 0.25 for i in range(HISTORY - 3)]
    closes += [closes[-1] - 6] * 3 + [closes[-1] + 1]
    for k in range(1, 30):
        days.append(day(k))
        closes.append(closes[-1] + 0.5)
    return dict(zip(days, closes))


BASE = _base_path()


class Market:
    """Serves only what was published by the requested end date; tests override single bars."""

    def __init__(self):
        self.overrides: dict[tuple[str, date], dict] = {}
        self.volumes: dict[tuple[str, date], int] = {}        # a spike makes a volume_breakout buy (v2.9 retriggers)
        self.filings: list[dict] = []
        self.earnings: dict[str, date] = {}
        self.calls: list[tuple] = []

    def bars(self, ticker, start, end_exclusive):
        end = end_exclusive - timedelta(days=1)
        self.calls.append(('bars', ticker, end))
        rows = []
        for d, close in BASE.items():
            if d > end:
                continue
            if not ticker.startswith('SYN') or ticker == 'SYNF':
                close = 500.0 if ticker == 'SPY' else 100.0          # SPY, ETFs and SYNF are flat
            bar = {'open': close, 'high': close, 'low': close, 'close': close}
            bar.update(self.overrides.get((ticker, d), {}))
            rows.append((d, bar))
        index = pd.DatetimeIndex([pd.Timestamp(d, tz='America/New_York') for d, _ in rows])
        return pd.DataFrame({'Open': [b['open'] for _, b in rows], 'High': [b['high'] for _, b in rows],
                             'Low': [b['low'] for _, b in rows], 'Close': [b['close'] for _, b in rows],
                             'Adj Close': [b['close'] for _, b in rows],
                             'Volume': [self.volumes.get((ticker, d), 1000) for d, _ in rows],
                             'Stock Splits': [0.0] * len(rows)}, index=index)

    def news(self, ticker):
        return []

    def discover(self, tickers, start, end):
        return [f for f in self.filings if f['ticker'] in tickers and start <= f['filing_date'] <= end]

    def earnings_calendar(self, ticker, **_):
        return {'Earnings Date': [self.earnings[ticker]] if ticker in self.earnings else []}

    def jump(self, ticker, d, factor):
        close = BASE[d] * factor
        self.overrides[(ticker, d)] = {'open': close, 'high': close, 'low': close, 'close': close}


class RoutedModel(BaseChatModel):
    """Replies come from one queue per prompt: propose (first version), revise, counter, review, other
    (interpretations). An exception in a queue is raised; a plain callable is a hook run while the model
    "thinks" (another transaction landing mid-call); an empty queue raises AssertionError."""
    queues: dict = {}
    calls: dict = {}
    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    @staticmethod
    def route(messages) -> str:
        system = messages[0].content if messages else ''
        human = messages[1].content if len(messages) > 1 else ''
        if system in (agent_graph.REVIEW_PROMPT, agent_graph.REVIEW_PROMPT_V2):
            return 'review'
        if system == agent_graph.COUNTER_PROMPT_V2:
            return 'counter'
        if system in (agent_graph.PROPOSE_PROMPT_V2, agent_graph.PROPOSE_PROMPT_V3):
            return 'revise' if '本次是修订' in human else 'propose'
        return 'other'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        route = self.route(messages)
        self.calls[route] = self.calls.get(route, 0) + 1
        self.seen.append((route, list(messages)))
        queue = self.queues.get(route) or []
        if not queue:
            raise AssertionError(f'no scripted reply for {route}')
        item = queue.pop(0)
        while callable(item) and not isinstance(item, BaseException):    # a hook runs mid-call, then the reply
            item()
            item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not item.usage_metadata:
            item.usage_metadata = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
        return ChatResult(generations=[ChatGeneration(message=item)])

    @property
    def _llm_type(self):
        return 'routed-test'


class QuotaError(Exception):
    status_code = 429

    def __init__(self):
        super().__init__('Error code: 429 - quota')
        self.body = {'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': '已达到 5 小时的使用上限'}}


class ConnectionLost(Exception):
    """The ChatGPT endpoint refusing the credential (HTTP 401): classified as ``not_connected``."""
    status_code = 401

    def __init__(self):
        super().__init__('Error code: 401')
        self.body = {'error': {'code': 'invalid_token', 'message': 'x'}}


class LateClock:
    """First call is the batch start; everything after is past the agent deadline."""

    def __init__(self):
        self.first = True

    def __call__(self):
        if self.first:
            self.first = False
            return 0.0
        return float(d_analysis_agent_runner.AGENT_DEADLINE_S)


def settings(**overrides):
    values = {'provider': 'chatgpt', 'model': 'gpt-test', 'api_key': 'sk-test',
              'daily_tokens': None, 'config_error': None}
    values.update(overrides)
    return agent_model_module.AgentSettings(**values)


# ---------------------------------------------------------------- scripted replies

def tool(name, args, call_id='t1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': call_id}])


def plan_json(*, stop=115.0, review_when=None, changes=None, action='buy', entry_type='zone_limit', max_hold=10,
              entry_low=120.0, entry_high=128.0, target1=140.0):
    if action == 'pass':
        return json.dumps({'action': 'pass', 'summary': '回撤后不再值得做',
                           'thesis': [{'text': '放量突破 20 日线', 'refs': ['call:1']}]}, ensure_ascii=False)
    out = {'action': 'buy', 'summary': '回踩修复放量',
           'entry_type': entry_type, 'max_hold_sessions': max_hold,
           'levels': {k: {'value': v, 'refs': ['call:1']} for k, v in
                      (('entry_low', entry_low), ('entry_high', entry_high), ('stop', stop), ('target1', target1))},
           'thesis': [{'text': '放量突破 20 日线', 'refs': ['call:1']}],
           'invalidation': [{'kind': 'close_below', 'params': {'price': 112}, 'refs': ['call:1']}]}
    if review_when is not None:
        out['review_when'] = review_when
    if changes is not None:
        out['changes'] = changes
    return json.dumps(out, ensure_ascii=False)


def proposal(ticker, **kwargs):
    return [tool('get_indicators', {'ticker': ticker}), AIMessage(content=plan_json(**kwargs))]


def counter():
    return [tool('get_news', {'days': 7}, 't2'),
            AIMessage(content=json.dumps({'counter': [{'thesis': 1, 'text': '量能不足', 'refs': ['call:1']}],
                                          'risks': []}, ensure_ascii=False))]


def verdict(ticker, outcome, reason='复查结论', *, cite=True, first=None):
    final = AIMessage(content=json.dumps({'outcome': outcome, 'reason': reason,
                                          'refs': ['call:1'] if cite else []}, ensure_ascii=False))
    if not cite:
        return [final]
    return [first or tool('get_price_bars', {'ticker': ticker, 'lookback': 5}, 'r1'), final]


STOP_UP = [{'field': 'stop', 'text': '止损上移到 117', 'refs': ['call:1']}]


def revision(ticker, **kwargs):
    kwargs.setdefault('stop', 117.0)
    kwargs.setdefault('changes', STOP_UP)
    return proposal(ticker, **kwargs)


# ---------------------------------------------------------------- batch helpers

def make_model(**queues):
    return RoutedModel(queues={k: list(v) for k, v in queues.items()}, calls={}, seen=[])


def run(db, market, k, *, tickers=None, pool=None, model=None, agent=None, clock=None, run_id=None, at=None):
    """One batch at the evening of day(k) (or at ``at``, later that evening) with a list-backed stages-2 recorder.

    Returns (summary, stage rows by id, model)."""
    model = model or make_model()
    rows: list[dict] = []
    run_id = run_id or f'run-{k}-{uuid.uuid4().hex[:6]}'
    now = at or evening(day(k))
    recorder = stages_module.StageRecorder(run_id, 'manual_batch', day(k), writer=rows.append,
                                           wall_now=lambda: now)
    deps = service.Deps(bar_fetcher=market.bars, news_fetcher=market.news, filing_source=market,
                        earnings_fetcher=market.earnings_calendar, universe=Universe('empty', S1, 'test', ()))
    deps.agent_model = model
    deps.agent_checkpointer = lambda: nullcontext(InMemorySaver())
    deps.agent_settings = agent or settings()
    deps.clock = clock
    summary = d_workflow_batch.run_batch(tickers=None if pool is not None else list(tickers), pool=pool,
                                now=now, deps=deps, run_id=run_id, stages=recorder)
    return summary, {row['stage']: row for row in rows}, model


def signal_day(db, market, tickers, *, review_when=None, pool=None, chains=None):
    """S1: each chain ticker (default: every ticker) gets version 0 — main list first, then extended,
    each alphabetical, the agent stage's order."""
    order = list(pool.core) + list(pool.extended) if pool is not None else sorted(chains or tickers)
    review_when = review_when or {}
    model = make_model(propose=[m for t in order for m in proposal(t, review_when=review_when.get(t))],
                       counter=[m for _ in order for m in counter()])
    summary, stage_rows, _ = run(db, market, 0, tickers=tickers, pool=pool, model=model)
    heads = {t: execution_repo.chain_head(db, t, S1) for t in order}
    assert all(h is not None and h['version']['version_no'] == 0 for h in heads.values()), summary['agent']
    return heads, stage_rows


def reviews(db, ticker='SYNTH'):
    return execution_repo.chain_reviews(db, ticker, S1)


def head(db, ticker='SYNTH'):
    return execution_repo.chain_head(db, ticker, S1)


def skips(row):
    return {g['reason']: g['samples'] for g in row['skips']}


def signal(row, kind):
    return next(s for s in row['signals'] if s['kind'] == kind)


def xmin(db, table, column, value):
    return db.execute(f'SELECT xmin::text FROM {table} WHERE {column} = %s', [value]).fetchone()[0]


# ---------------------------------------------------------------- objects and host verdicts

def test_fresh_version_not_reviewed_same_run(db):
    market = Market()
    model = make_model(propose=proposal('SYNTH'), counter=counter())
    summary, rows, _ = run(db, market, 0, tickers=['SYNTH'], model=model)
    assert head(db)['version']['version_no'] == 0
    review = rows['review']
    assert review['status'] == 'success' and review['input_count'] == 0 and review['output_count'] == 0
    assert skips(review) == {'fresh_version': ['SYNTH']}
    assert review['detail']['excluded'] == [{'ticker': 'SYNTH', 'signal_date': S1.isoformat(),
                                             'reason': 'fresh_version'}]
    assert reviews(db) == []
    assert summary['review']['objects'] == 0
    assert model.calls.get('review', 0) == 0


def test_quiet_day_host_maintain_without_model(db):
    market = Market()
    signal_day(db, market, ['SYNTH'], review_when={'SYNTH': QUIET})
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'])
    [row] = reviews(db)
    assert (row['outcome'], row['decided_by'], row['trigger'], row['mode']) == ('maintain', 'host', 'batch', 'entry')
    assert row['reason'] == f'无新信号，复查条件未满足（到 {FAR} 再看）'
    assert row['review_agent_run_id'] is None and row['attempt'] == 1 and row['session'] == day(1)
    assert [s['kind'] for s in row['signals']] == list(plan_review.timeline.SIGNAL_KINDS_V2)
    assert not any(s['fired'] for s in row['signals'])
    assert [(c['list'], c['kind'], c['result']) for c in row['conditions']] == [
        ('invalidation', 'close_below', 'not_met'), ('review_when', 'date_reached', 'not_met')]
    assert model.calls.get('review', 0) == 0
    assert rows['review']['input_count'] == 1 and rows['review']['output_count'] == 1
    assert rows['review']['outputs']['review_ids'] == [row['id']]
    assert rows['review']['detail']['decided_by'] == {'agent': 0, 'host': 1}
    assert rows['review']['detail']['modes'] == {'entry': 1, 'holding': 0}
    assert summary['review']['outcomes']['maintain'] == 1 and summary['review']['objects'] == 1


def test_default_next_session_calls_model(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'],
                               model=make_model(review=verdict('SYNTH', 'maintain', '趋势未变', cite=False)))
    assert model.calls == {'review': 1}
    [row] = reviews(db)
    assert (row['outcome'], row['decided_by'], row['reason']) == ('maintain', 'agent', '趋势未变')
    run_row = data_repo.agent_run_by_id(db, row['review_agent_run_id'])
    assert (run_row['mode'], run_row['agent_version'], run_row['status']) == ('review', 'agent-review-2', 'ok')
    assert run_row['plan_id'] is None and run_row['cutoff_at'] == evening(day(1))
    assert row['input_tokens'] == 10 and row['output_tokens'] == 5
    assert [(c['kind'], c['result']) for c in row['conditions'] if c['list'] == 'review_when'] == \
        [('next_session', 'met')]
    assert rows['review']['outputs']['agent_run_ids'] == [str(row['review_agent_run_id'])]
    assert summary['review']['tokens'] == {'input': 10, 'output': 5}


def _setup_new_8k(market, monkeypatch):
    market.filings.append({'ticker': 'SYNTH', 'form': '8-K', 'filing_date': day(1).isoformat(), 'items': ['8.01'],
                           'accepted_at': f'{day(1).isoformat()}T08:00:00-04:00',
                           'edgar_url': 'https://www.sec.gov/Archives/edgar/data/0/synth-8k.htm'})
    return 1


def _setup_abnormal(market, monkeypatch):
    market.jump('SYNTH', day(1), 1.10)
    return 1


def _setup_stop(market, monkeypatch):
    market.overrides[('SYNTH', day(1))] = {'low': 114.0}
    return 1


def _setup_t1(market, monkeypatch):
    market.overrides[('SYNTH', day(1))] = {'high': 141.0}
    return 1


def _setup_expiry(market, monkeypatch):
    return 2                                   # S+1 == valid_until (S1+3)


def _setup_regime(market, monkeypatch):
    real = regime_module.snapshot

    def snapshot(**kwargs):
        return {**real(**kwargs), 'label': 'risk_on' if kwargs['session'] == S1 else 'risk_off'}

    monkeypatch.setattr(regime_module, 'snapshot', snapshot)
    return 1


SIGNAL_SETUPS = {'new_8k': _setup_new_8k, 'abnormal_move': _setup_abnormal, 'stop_touched': _setup_stop,
                 't1_touched': _setup_t1, 'expiry_near': _setup_expiry, 'regime_change': _setup_regime}


@pytest.mark.parametrize('kind', list(SIGNAL_SETUPS))
def test_signal_triggers_revision(db, monkeypatch, kind):
    market = Market()
    k = SIGNAL_SETUPS[kind](market, monkeypatch)
    v0 = signal_day(db, market, ['SYNTH'], review_when={'SYNTH': QUIET})[0]['SYNTH']
    session = day(k)
    model = make_model(review=verdict('SYNTH', 'revise', '止损应上移'), revise=revision('SYNTH', review_when=QUIET),
                       counter=counter())
    summary, rows, model = run(db, market, k, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    assert signal(row, kind)['fired'] is True, row['signals']
    assert (row['outcome'], row['decided_by'], row['reason']) == ('revise', 'agent', '止损应上移')
    new = head(db)
    version, plan = new['version'], new['plan']
    assert version['version_no'] == 1 and version['origin'] == 'review'
    assert str(version['supersedes_plan_id']) == str(v0['plan']['id'])
    assert version['as_of_session'] == session and version['cutoff_at'] == evening(session)
    assert version['diff'] == [
        {'field': 'stop', 'before': '115.00', 'after': '117.00', 'host_only': False},      # the model's change ...
        {'field': 'valid_until', 'before': v0['plan']['valid_until'].isoformat(),         # ... and the host field (D7)
         'after': plan['valid_until'].isoformat(), 'host_only': True}]
    assert plan['valid_from'] == add_trading_days(session, 1) and plan['status'] == 'ok'
    assert plan['plan_version'] == 'agent-3' and plan['stop'] == 117
    assert (plan['entry_type'], plan['max_hold_sessions']) == ('zone_limit', 10)
    assert version['schema_version'] == 'agent-3' and plan['inputs']['chain_mode'] == 'entry'
    assert str(row['new_plan_id']) == str(plan['id'])
    revise_run = data_repo.agent_run_by_id(db, row['revision_agent_run_id'])
    assert (revise_run['mode'], revise_run['agent_version'], revise_run['status']) == ('revise', 'agent-3', 'ok')
    assert str(revise_run['plan_id']) == str(plan['id']) and str(version['agent_run_id']) == str(revise_run['id'])
    assert {c['phase'] for c in analysis_repo.agent_tool_calls(db, revise_run['id'])} == {'propose', 'counter'}
    assert [c['phase'] for c in analysis_repo.agent_tool_calls(db, row['review_agent_run_id'])] == ['review']
    # plan, version, revise run, review run and review row: one transaction
    same = {xmin(db, 'trade_plans', 'id', plan['id']), xmin(db, 'agent_plan_versions', 'plan_id', plan['id']),
            xmin(db, 'agent_runs', 'id', revise_run['id']), xmin(db, 'agent_runs', 'id', row['review_agent_run_id']),
            xmin(db, 'plan_reviews', 'id', row['id'])}
    assert len(same) == 1
    assert row['input_tokens'] == 60 and row['output_tokens'] == 30         # 2 次复查回复 + 4 次修订回复
    stage = rows['review']
    assert stage['status'] == 'success' and stage['output_count'] == 1
    assert stage['outputs']['plan_ids'] == [str(plan['id'])]
    assert stage['detail']['derivation']['consistent'] == 1 and stage['detail']['derivation']['checked'] == 1
    assert stage['detail']['signals'][kind] == 1
    assert summary['review']['outcomes']['revise'] == 1


def test_invalidation_met_host_invalidates(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    market.jump('SYNTH', day(1), 110.0 / BASE[day(1)])
    _, rows, model = run(db, market, 1, tickers=['SYNTH'])
    [row] = reviews(db)
    assert (row['outcome'], row['decided_by']) == ('invalidate', 'host')
    assert row['reason'] == '失效条件成立：收盘跌破 112.00'
    assert model.calls.get('review', 0) == 0 and row['review_agent_run_id'] is None
    assert rows['review']['detail']['decided_by'] == {'agent': 0, 'host': 1}

    summary, rows, _ = run(db, market, 2, tickers=['SYNTH'])               # 次日：链已作废，不再是对象
    assert rows['review']['input_count'] == 0 and summary['review']['objects'] == 0
    assert len(reviews(db)) == 1


def test_revision_failure_keeps_head(db):
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    bad = [{'field': 'entry', 'text': '入场下移', 'refs': ['call:1']}]          # 实际只改了止损
    model = make_model(review=verdict('SYNTH', 'revise', '止损应上移'), revise=revision('SYNTH', changes=bad),
                       counter=counter())
    summary, rows, _ = run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    assert row['outcome'] == 'revise' and row['new_plan_id'] is None
    assert 'changes_mismatch' in row['detail']['revision_status']
    assert row['detail']['revision_status'] == 'rejected: changes_mismatch'
    assert str(head(db)['plan']['id']) == str(v0['plan']['id'])
    revise_run = data_repo.agent_run_by_id(db, row['revision_agent_run_id'])
    assert (revise_run['status'], revise_run['reason'], revise_run['plan_id']) == ('rejected', 'changes_mismatch', None)
    stage = rows['review']
    assert stage['status'] == 'degraded' and [d['kind'] for d in stage['degradations']] == ['revision_failed']
    assert stage['output_count'] == 1 and stage['outputs']['plan_ids'] == []
    assert db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'agent'").fetchone()[0] == 1


def test_revision_pass_becomes_invalidate(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    model = make_model(review=verdict('SYNTH', 'revise', '形态走坏'), revise=revision('SYNTH', action='pass'),
                       counter=counter())
    run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    assert (row['outcome'], row['decided_by']) == ('invalidate', 'agent')
    assert row['detail']['via'] == 'revision_pass' and row['new_plan_id'] is None
    assert data_repo.agent_run_by_id(db, row['revision_agent_run_id'])['status'] == 'pass'
    summary, rows, _ = run(db, market, 2, tickers=['SYNTH'])
    assert summary['review']['objects'] == 0 and len(reviews(db)) == 1


def test_entry_revision_invalid_is_failed_revision(db):
    market = Market()
    market.earnings['SYNTH'] = day(4)               # 首版有效期 S1+1..S1+3 之外，修订版 S1+2..S1+4 之内
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    assert v0['plan']['status'] == 'ok'
    model = make_model(review=verdict('SYNTH', 'revise', '止损应上移'), revise=revision('SYNTH'), counter=counter())
    _, rows, _ = run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    assert row['outcome'] == 'revise' and row['new_plan_id'] is None
    assert row['detail']['revision_status'].startswith('plan_invalid: earnings_in_validity')
    assert str(head(db)['plan']['id']) == str(v0['plan']['id'])
    assert db.execute("SELECT count(*) FROM trade_plans WHERE kind = 'agent'").fetchone()[0] == 1
    revise_run = data_repo.agent_run_by_id(db, row['revision_agent_run_id'])
    assert (revise_run['status'], revise_run['reason']) == ('rejected', 'plan_invalid')
    assert [d['kind'] for d in rows['review']['degradations']] == ['revision_failed']


def test_review_tool_context_uses_review_session(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    market.jump('SYNTH', day(1), 1.10)                     # S 当日的新候选：abnormal_move
    model = make_model(review=verdict('SYNTH', 'maintain', '异动但趋势未破', first=tool('get_trigger', {}, 'r1')))
    run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    [call] = analysis_repo.agent_tool_calls(db, row['review_agent_run_id'])
    assert call['tool'] == 'get_trigger' and call['phase'] == 'review'
    assert [c['rule_id'] for c in call['result']['data']] == ['abnormal_move']       # S 当日的候选
    assert {c['inputs']['signal_date'] for c in call['result']['data']} == {day(1).isoformat()}
    first = next(messages for route, messages in model.seen if route == 'review')
    prompt = first[1].content
    assert f'复查交易日 {day(1)}' in prompt
    trigger = prompt.split('## 信号日原始触发')[1].split('## 系统信号')[0]
    assert 'trend_reclaim（buy）' in trigger and S1.isoformat() in trigger            # 信号日的原始触发
    assert day(1).isoformat() not in trigger


# ---------------------------------------------------------------- holdings, ends, ordering

def _decide(db, ticker, final, *, plan_id=None, now):
    cands = data_repo.unit_candidates(db, ticker, S1)
    return legacy_decisions.decide({'request_id': str(uuid.uuid4()), 'ticker': ticker, 'signal_date': S1.isoformat(),
                           'candidate_ids': [str(c['id']) for c in cands], 'proposed_action': 'buy',
                           'final_action': final, 'decision': 'accept' if final == 'buy' else 'reject',
                           'reason': '合成样例', 'plan_id': str(plan_id) if plan_id else None}, now=now)['record']


def _trade(ticker, side, quantity, price, d, decision_id=None):
    return {'request_id': str(uuid.uuid4()), 'ticker': ticker, 'side': side, 'quantity': quantity, 'price': price,
            'executed_at': datetime.combine(d, time(10, 0), tzinfo=NY).isoformat(),
            'decision_id': str(decision_id) if decision_id else None}


def test_holding_chain_reviewed_after_invalid_version(db):
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    decision = _decide(db, 'SYNTH', 'buy', plan_id=v0['plan']['id'], now=evening(S1))
    legacy_decisions.add_trade(_trade('SYNTH', 'buy', '10', '125', day(1), decision['id']), now=evening(day(1)))
    execution_trades.add_snapshot({'request_id': str(uuid.uuid4()), 'as_of': day(1).isoformat(), 'equity': '1000'},
                         now=evening(day(1)) - timedelta(hours=1))   # 已持有 1250 > 单票上限 → 修订版 no_room
    model = make_model(review=verdict('SYNTH', 'revise', '止损应上移'), revise=revision('SYNTH'), counter=counter())
    run(db, market, 1, tickers=['SYNTH'], model=model)
    [row] = reviews(db)
    assert row['mode'] == 'holding' and row['outcome'] == 'revise' and row['new_plan_id'] is not None
    new = head(db)
    assert new['version']['version_no'] == 1 and str(new['plan']['id']) == str(row['new_plan_id'])
    assert new['plan']['status'] == 'invalid' and new['plan']['invalid_reasons'] == ['no_room']
    assert new['plan']['inputs']['chain_entry_cap'] is None

    _, rows, _ = run(db, market, 2, tickers=['SYNTH'],
                     model=make_model(review=verdict('SYNTH', 'maintain', '持仓照常', cite=False)))
    second = reviews(db)[-1]
    assert second['session'] == day(2) and second['mode'] == 'holding' and second['outcome'] == 'maintain'
    assert str(second['head_plan_id']) == str(row['new_plan_id'])
    assert rows['review']['detail']['modes'] == {'entry': 0, 'holding': 1}


def test_chain_ends(db):
    market = Market()
    heads = signal_day(db, market, ['SYNA', 'SYNB', 'SYNC'])[0]
    _decide(db, 'SYNA', 'ignore', now=evening(S1))                                         # 否决
    decision = _decide(db, 'SYNC', 'buy', plan_id=heads['SYNC']['plan']['id'], now=evening(S1))
    legacy_decisions.add_trade(_trade('SYNC', 'buy', '10', '125', day(1), decision['id']), now=evening(day(1)))
    legacy_decisions.add_trade(_trade('SYNC', 'sell', '10', '126', day(2)), now=evening(day(2)))    # 平仓
    summary, rows, model = run(db, market, 3, tickers=['SYNA', 'SYNB', 'SYNC'])            # SYNB 已到截止日
    stage = rows['review']
    assert stage['input_count'] == 0 and summary['review']['objects'] == 0
    assert stage['detail']['ended'] == {'expired': 1, 'invalidated': 0, 'closed_by_decision': 1, 'position_closed': 1,
                                        'superseded': 0}
    assert stage['detail']['holdings_without_chain'] == []
    assert model.calls.get('review', 0) == 0 and reviews(db, 'SYNA') == reviews(db, 'SYNB') == []


def test_invalidated_holding_chain_is_not_a_holding_without_chain(db):
    """D7: a holding whose unit's agent-2 chain was invalidated has a chain — it is just no longer reviewed —
    so only a holding with no chain at all is listed in holdings_without_chain."""
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']
    decision = _decide(db, 'SYNTH', 'buy', plan_id=v0['plan']['id'], now=evening(S1))
    legacy_decisions.add_trade(_trade('SYNTH', 'buy', '10', '125', day(1), decision['id']), now=evening(day(1)))
    legacy_decisions.add_trade(_trade('SYNB', 'buy', '5', '90', day(1)), now=evening(day(1)))      # 无链的持仓
    execution_repo.insert_plan_review(db, {
        'ticker': 'SYNTH', 'signal_date': S1, 'head_plan_id': v0['plan']['id'], 'session': day(1),
        'trigger': 'batch', 'run_id': 'run-x', 'mode': 'holding', 'timeline_version': 'timeline-1',
        'outcome': 'invalidate', 'decided_by': 'host', 'reason': '失效条件成立', 'attempt': 1,
        'cutoff_at': evening(day(1))})
    db.commit()
    _, rows, model = run(db, market, 2, tickers=['SYNTH'])
    assert model.calls.get('review', 0) == 0
    assert rows['review']['detail']['holdings_without_chain'] == ['SYNB']
    assert execution_chains.held_chain_states(db, day(2)) == {'SYNTH': 'invalidated'}


def test_extended_pool_chain_reviewed_last(db, monkeypatch):
    market = Market()
    db.execute('INSERT INTO pool_tiers (ticker, tier, effective_session, recorded_at) '
               "VALUES ('SYNA', 'extended', %s, %s)", [S1 - timedelta(days=1), evening(S1) - timedelta(days=1)])
    pool = pool_module.Pool(core=('SYNTH',), extended=('SYNA',))
    signal_day(db, market, None, pool=pool)
    assert {c['ticker']: c['pool'] for c in execution_repo.all_chains(db)} == {'SYNA': 'extended', 'SYNTH': 'main'}
    model = make_model(review=[*verdict('SYNTH', 'maintain', '主列表', cite=False),
                               *verdict('SYNA', 'maintain', '扩展池', cite=False)])
    run(db, market, 1, pool=pool, model=model)
    rows = execution_repo.reviews_on(db, day(1))
    assert [(r['ticker'], r['reason']) for r in rows] == [('SYNTH', '主列表'), ('SYNA', '扩展池')]

    monkeypatch.setattr(execution_chains, 'REVIEW_MAX_OBJECTS', 1)
    _, stage_rows, _ = run(db, market, 2, pool=pool,
                           model=make_model(review=verdict('SYNTH', 'maintain', '主列表', cite=False)))
    assert [r['ticker'] for r in execution_repo.reviews_on(db, day(2))] == ['SYNTH']
    assert skips(stage_rows['review']) == {'object_cap': ['SYNA']}
    assert stage_rows['review']['detail']['excluded'] == [
        {'ticker': 'SYNA', 'signal_date': S1.isoformat(), 'reason': 'object_cap'}]
    assert stage_rows['review']['input_count'] == 1


# ---------------------------------------------------------------- budgets, re-runs, locks

@pytest.mark.parametrize('gate', ['stage_budget', 'quota_exhausted', 'token_budget', 'not_configured'])
def test_budget_skips(db, monkeypatch, gate):
    market = Market()
    signal_day(db, market, ['SYNQ', 'SYNTH'], review_when={'SYNQ': QUIET})     # SYNQ 宿主可判定，SYNTH 要调模型
    kwargs = {}
    if gate == 'stage_budget':
        kwargs['clock'] = LateClock()
    elif gate == 'quota_exhausted':                # 同一次批处理里 agent 阶段先遇到额度用尽
        market.jump('SYNTH', day(1), 1.10)
        kwargs['model'] = make_model(other=[QuotaError()])
    elif gate == 'token_budget':
        kwargs['agent'] = settings(daily_tokens=1000)
        monkeypatch.setattr(analysis_repo, 'agent_tokens_on', lambda conn, ny_date: 10 ** 6)
    else:
        kwargs['agent'] = settings(connection='no_sharing')      # signed in, but no plan usage shared: not_configured
    summary, rows, model = run(db, market, 1, tickers=['SYNQ', 'SYNTH'], **kwargs)
    assert model.calls.get('review', 0) == 0
    [quiet] = reviews(db, 'SYNQ')
    assert (quiet['outcome'], quiet['decided_by']) == ('maintain', 'host')
    [skipped] = reviews(db, 'SYNTH')
    assert (skipped['outcome'], skipped['decided_by'], skipped['reason']) == ('skipped', 'host', gate)
    assert skipped['attempt'] == 1 and skipped['review_agent_run_id'] is None
    stage = rows['review']
    assert skips(stage)[gate] == ['SYNTH'] and stage['output_count'] == 1
    assert summary['review']['outcomes'] == {'maintain': 1, 'revise': 0, 'invalidate': 0, 'skipped': 1, 'error': 0}
    if gate == 'not_configured':
        assert stage['status'] == 'degraded'
        assert stage['degradations'] == [{'kind': 'agent_unavailable', 'detail': 'not_configured'}]
    else:
        assert stage['status'] == 'success'
    if gate != 'stage_budget':
        return
    for attempt in (2, 3, 4):                       # 同 S 重跑：最多 3 行，第 4 次只在 skips 记 max_attempts
        _, rows, _ = run(db, market, 1, tickers=['SYNQ', 'SYNTH'], clock=LateClock())
        assert [r['attempt'] for r in reviews(db, 'SYNTH')] == list(range(1, min(attempt, 3) + 1))
        assert skips(rows['review'])['already_reviewed'] == ['SYNQ']
    assert skips(rows['review'])['max_attempts'] == ['SYNTH']
    assert len(reviews(db, 'SYNQ')) == 1


def test_review_stage_records_its_own_quota_exhaustion(db):
    """The retro stage reads this flag: a quota hit in the review stage itself must reach ``summary``."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    market.jump('SYNTH', day(1), 1.10)
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'], model=make_model(review=[QuotaError()]))
    [row] = reviews(db, 'SYNTH')
    assert (row['outcome'], row['reason']) == ('error', 'quota_exhausted')
    assert summary['review']['quota_exhausted'] is True
    summary, rows, model = run(db, market, 2, tickers=['SYNTH'])           # nothing to ask the model: no flag
    assert not summary['review'].get('quota_exhausted')


def test_connection_lost_in_the_review_skips_the_other_chains(db):
    """Spec §8.8: the first review call that finds the connection unusable is an ``error`` row; the chains after it are
    skipped with the same reason and cost no model call (like a quota hit)."""
    market = Market()
    signal_day(db, market, ['SYNA', 'SYNTH'])
    for ticker in ('SYNA', 'SYNTH'):
        market.jump(ticker, day(1), 1.10)
    summary, rows, model = run(db, market, 1, tickers=['SYNA', 'SYNTH'], model=make_model(review=[ConnectionLost()]))
    assert model.calls.get('review', 0) == 1
    [first] = reviews(db, 'SYNA')
    assert (first['outcome'], first['reason']) == ('error', 'not_connected')
    [second] = reviews(db, 'SYNTH')
    assert (second['outcome'], second['decided_by'], second['reason']) == ('skipped', 'host', 'not_connected')
    assert second['review_agent_run_id'] is None
    assert skips(rows['review'])['not_connected'] == ['SYNTH']


def test_connection_lost_in_the_agent_stage_reaches_the_review(db):
    """The agent stage found the connection unusable (an interpretation call): the review stage asks nothing either."""
    market = Market()
    signal_day(db, market, ['SYNTH'])
    market.jump('SYNTH', day(1), 1.10)
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'], model=make_model(other=[ConnectionLost()]))
    assert summary['agent']['connection_lost'] == 'not_connected'
    assert model.calls.get('review', 0) == 0
    [skipped] = reviews(db, 'SYNTH')
    assert (skipped['outcome'], skipped['reason']) == ('skipped', 'not_connected')


def test_rerun_same_session_already_reviewed(db):
    market = Market()
    signal_day(db, market, ['SYNTH'])
    run(db, market, 1, tickers=['SYNTH'], model=make_model(review=verdict('SYNTH', 'maintain', '照旧', cite=False)))
    assert len(reviews(db)) == 1
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'])
    assert len(reviews(db)) == 1 and model.calls.get('review', 0) == 0
    assert skips(rows['review']) == {'already_reviewed': ['SYNTH']} and rows['review']['input_count'] == 0
    assert summary['review']['objects'] == 0


def test_review_lock_held_skips_without_row(db):
    """A recheck holding review:<ticker>:<signal_date> makes the batch skip the chain without a row."""
    import os

    import psycopg

    market = Market()
    signal_day(db, market, ['SYNTH'])
    with psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']) as other:
        other.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', [f'review:SYNTH:{S1.isoformat()}'])
        summary, rows, model = run(db, market, 1, tickers=['SYNTH'])
        other.rollback()
    assert reviews(db) == [] and model.calls.get('review', 0) == 0
    assert skips(rows['review']) == {'review_in_progress': ['SYNTH']}
    assert rows['review']['detail']['excluded'] == [
        {'ticker': 'SYNTH', 'signal_date': S1.isoformat(), 'reason': 'review_in_progress'}]
    assert summary['review']['objects'] == 1


def test_run_lock_held_skips_without_row(db):
    """A review of the chain running elsewhere holds the session-level run lock review-run:<ticker>:<date>
    for its whole run (model calls included): the batch skips the chain before reading or calling the model."""
    import os

    import psycopg

    market = Market()
    signal_day(db, market, ['SYNTH'])
    with psycopg.connect(os.environ['INVESTMENT_ASSISTANT_TEST_DATABASE_URL']) as other:
        other.execute('SELECT pg_advisory_lock(hashtext(%s))', [f'review-run:SYNTH:{S1.isoformat()}'])
        summary, rows, model = run(db, market, 1, tickers=['SYNTH'])
        other.execute('SELECT pg_advisory_unlock_all()')
    assert reviews(db) == [] and model.calls.get('review', 0) == 0
    assert skips(rows['review']) == {'review_in_progress': ['SYNTH']}


def test_chain_ticker_collected_without_rules(db):
    market = Market()
    signal_day(db, market, ['SYNF', 'SYNTH'], chains=['SYNTH'])     # SYNF 平盘，没有候选
    market.jump('SYNTH', day(1), 1.10)              # 若跑规则就会产生 abnormal_move 候选
    model = make_model(review=verdict('SYNTH', 'maintain', '照旧', cite=False))
    summary, rows, _ = run(db, market, 1, tickers=['SYNF'], model=model)     # SYNTH 已不在观察池
    assert summary['pool']['chains'] == 1
    assert ('bars', 'SYNTH', day(1)) in market.calls
    assert db.execute('SELECT count(*) FROM price_bars WHERE ticker = %s AND price_date = %s',
                      ['SYNTH', day(1)]).fetchone()[0] == 1
    earnings = rows['collect']['detail']['earnings']          # v2.8: the per-target items ride along
    assert {k: earnings[k] for k in ('targets', 'ok', 'failed')} == {'targets': 2, 'ok': 2, 'failed': 0}
    assert sorted(item['target'] for item in earnings['items']) == ['NVDA', 'SYNTH'] or len(earnings['items']) == 2
    assert summary['tickers']['SYNTH']['candidates'] == []
    assert 'SYNTH' in summary['earnings']
    assert db.execute('SELECT count(*) FROM signal_candidates WHERE ticker = %s AND signal_date = %s',
                      ['SYNTH', day(1)]).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM trade_plans WHERE ticker = %s AND signal_date = %s',
                      ['SYNTH', day(1)]).fetchone()[0] == 0
    assert analysis_repo.latest_brief(db, 'SYNTH', day(1)) is None
    assert rows['signals']['input_count'] == 1                      # 规则循环只跑 SYNF
    [row] = reviews(db)
    assert row['session'] == day(1) and signal(row, 'abnormal_move')['fired'] is False


# ---------------------------------------------------------------- fix round 1: races, isolation, failed writes

@pytest.mark.parametrize('winner', ['invalidate', 'decision'])
def test_chain_ended_while_model_runs_writes_nothing(db, winner):
    """Review Focus 5: while the batch's model call runs, a review row written without the run lock (as a
    stand-in for any writer that moves the chain) invalidates the head, or a non-buy decision lands; the
    batch's write transaction sees the chain no longer active and writes no version and no review row
    (review_in_progress). Its model runs are kept as ``skipped`` runs with their tokens, so the daily token
    cap still counts them."""
    market = Market()
    v0 = signal_day(db, market, ['SYNTH'])[0]['SYNTH']

    def other_side():
        if winner == 'invalidate':
            execution_repo.insert_plan_review(db, {
                'ticker': 'SYNTH', 'signal_date': S1, 'head_plan_id': v0['plan']['id'], 'session': day(1),
                'trigger': 'recheck', 'run_id': 'recheck-1', 'mode': 'entry',
                'timeline_version': 'timeline-1', 'outcome': 'invalidate', 'decided_by': 'host',
                'reason': '失效条件成立：重新复查', 'attempt': 1, 'cutoff_at': evening(day(1)) - timedelta(minutes=5)})
            db.commit()
        else:
            _decide(db, 'SYNTH', 'ignore', now=evening(day(1)) - timedelta(minutes=5))

    model = make_model(review=[other_side, *verdict('SYNTH', 'revise', '止损应上移')], revise=revision('SYNTH'),
                       counter=counter())
    summary, rows, model = run(db, market, 1, tickers=['SYNTH'], model=model)
    assert model.calls['revise'] == 2                     # the losing side did run its revision
    assert [v['version_no'] for v in execution_repo.chain_versions(db, 'SYNTH', S1)] == [0]
    batch_rows = [r for r in reviews(db) if r['trigger'] == 'batch']
    assert batch_rows == []
    lost = db.execute("SELECT mode, status, reason, input_tokens, output_tokens, plan_id FROM agent_runs "
                      "WHERE mode IN ('review', 'revise') ORDER BY mode").fetchall()
    assert lost == [('review', 'skipped', 'review_in_progress', 20, 10, None),
                    ('revise', 'skipped', 'review_in_progress', 40, 20, None)]
    assert analysis_repo.agent_tokens_on(db, day(1)) >= 90
    assert skips(rows['review']) == {'review_in_progress': ['SYNTH']}
    assert summary['review']['outcomes'] == {'maintain': 0, 'revise': 0, 'invalidate': 0, 'skipped': 0, 'error': 0}


def test_broken_chain_does_not_stop_the_others(db, monkeypatch):
    """§7.4 / §13: one chain whose state read fails (a database error here, so the read needs a savepoint)
    is reported; the other chain is still collected and reviewed."""
    market = Market()
    signal_day(db, market, ['SYNB', 'SYNF', 'SYNTH'], chains=['SYNB', 'SYNTH'])
    real = execution_chains.chain_status

    def chain_status(conn, ticker, signal_date, **kwargs):
        if ticker == 'SYNB':
            conn.execute('SELECT * FROM no_such_table_for_synb')        # aborts the transaction without a savepoint
        return real(conn, ticker, signal_date, **kwargs)

    monkeypatch.setattr(execution_chains, 'chain_status', chain_status)
    model = make_model(review=verdict('SYNTH', 'maintain', '照旧', cite=False))
    summary, rows, _ = run(db, market, 1, tickers=['SYNF'], model=model)     # both chain tickers left the pool
    assert summary['pool']['chains'] == 1
    assert ('bars', 'SYNTH', day(1)) in market.calls and ('bars', 'SYNB', day(1)) not in market.calls
    assert [f['target'] for f in rows['collect']['failures']] == ['chains:SYNB']
    [row] = reviews(db, 'SYNTH')
    assert row['outcome'] == 'maintain' and reviews(db, 'SYNB') == []
    stage = rows['review']
    assert stage['status'] == 'partial' and [f['target'] for f in stage['failures']] == ['SYNB']
    assert stage['output_count'] == 1 and stage['input_count'] == 1
    assert [e for e in summary['errors'] if e.startswith('SYNB review: ')]


def test_failed_write_still_counts_attempt_and_tokens(db, monkeypatch):
    """A failure inside the write transaction rolls back the version, then an error row is written with the
    model runs as error runs (tokens kept), so a rerun on the same S is attempt 2 and the cap sees the tokens."""
    market = Market()
    signal_day(db, market, ['SYNTH'])

    def boom(**kwargs):
        raise RuntimeError('plan build exploded')

    monkeypatch.setattr(plan_review.timeline, 'build_version_plan', boom)
    for attempt in (1, 2):
        model = make_model(review=verdict('SYNTH', 'revise', '止损应上移'), revise=revision('SYNTH'),
                           counter=counter())
        summary, rows, _ = run(db, market, 1, tickers=['SYNTH'], model=model)
        row = reviews(db)[-1]
        assert (row['outcome'], row['decided_by'], row['attempt']) == ('error', 'agent', attempt)
        assert 'plan build exploded' in row['detail']['write_error'] and row['new_plan_id'] is None
        assert (row['input_tokens'], row['output_tokens']) == (60, 30)
        runs = [data_repo.agent_run_by_id(db, row['review_agent_run_id']),
                data_repo.agent_run_by_id(db, row['revision_agent_run_id'])]
        assert [(r['mode'], r['status'], r['input_tokens']) for r in runs] == [('review', 'error', 20),
                                                                              ('revise', 'error', 40)]
        assert [f['target'] for f in rows['review']['failures']] == ['SYNTH']
        assert [e for e in summary['errors'] if e.startswith('SYNTH review: ')]
    assert [v['version_no'] for v in execution_repo.chain_versions(db, 'SYNTH', S1)] == [0]
    assert analysis_repo.agent_tokens_on(db, day(1)) >= 120


def test_chain_tickers_requires_exclude():
    import inspect

    param = inspect.signature(execution_chains.chain_tickers).parameters['exclude']
    assert param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty
