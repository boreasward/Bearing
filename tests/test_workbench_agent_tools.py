"""Agent tool layer: recording, bounds, indicator math and cutoff visibility. No network."""
from investment_assistant.execution import chains as execution_chains
import investment_assistant.core.schema as core_schema
import investment_assistant.analysis.repo as analysis_repo
import investment_assistant.data.repo as data_repo
import investment_assistant.execution.repo as execution_repo
import investment_assistant.execution.repo_legacy as execution_repo_legacy
import investment_assistant.review.repo as review_repo
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from investment_assistant.data.calendar import add_trading_days
from investment_assistant.analysis.agent import REASON_TEXT, STATUSES
from investment_assistant.analysis.agent.tools import (MAX_LOOKBACK, PRICE_TOOLS, Recorder, ToolContext,
                                                        ToolError, call, get_price_bars, get_trigger,
                                                        result_hash)
from investment_assistant.derivation.indicators.price import indicators

S = date(2026, 10, 21)
CUTOFF = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)


def ctx(ticker='AMD', read=lambda fn: None):
    return ToolContext(ticker=ticker, session=S, cutoff_at=CUTOFF, read=read)


def test_reason_text_covers_every_status_reason():
    reasons = {'disabled', 'not_configured', 'quota_exhausted', 'token_budget', 'stage_budget', 'max_attempts',
               'malformed', 'invalid_citation', 'level_check', 'tool_rounds_exceeded', 'model_error',
               'checkpoint_unavailable', 'thesis_count', 'invalid_condition', 'changes_mismatch', 'no_change',
               'review_malformed', 'migration_026_missing', 'review_in_progress', 'plan_invalid',
               'retro_malformed', 'lesson_invalid', 'answer_malformed', 'migration_027_missing',
               'discussion_busy', 'draft_exists', 'not_connected', 'model_unavailable',
               'invalid_entry_type', 'invalid_max_hold', 'stop_lowered', 'stop_above_close', 'active_chain'}
    assert set(REASON_TEXT) == reasons
    assert {k: REASON_TEXT[k] for k in ('invalid_entry_type', 'invalid_max_hold', 'stop_lowered', 'stop_above_close',
                                        'active_chain')} == {      # v2.9 spec §5.5 + R3, verbatim
        'invalid_entry_type': '入场类型不在允许的三种之内', 'invalid_max_hold': '最长持有须为 5–20 个交易日',
        'stop_lowered': '持仓期不能下移止损', 'stop_above_close': '持仓期止损不能高于最新收盘',
        'active_chain': '已有进行中的计划链，这次触发已并入'}
    assert REASON_TEXT['not_connected'] == 'ChatGPT 未连接' and REASON_TEXT['model_unavailable'] == '所选模型不可用'
    assert REASON_TEXT['plan_invalid'] == '修订版按规则作废'
    assert {k: REASON_TEXT[k] for k in reasons if k in ('retro_malformed', 'lesson_invalid', 'answer_malformed',
                                                       'migration_027_missing', 'discussion_busy', 'draft_exists')} == {
        'retro_malformed': '复盘卡草稿格式无效', 'lesson_invalid': '教训草稿缺适用范围、反例或挂靠无效',
        'answer_malformed': '回答格式无效', 'migration_027_missing': '迁移 027 未执行',
        'discussion_busy': '上一个问题还在回答', 'draft_exists': '这张卡已有成功的草稿'}
    assert STATUSES == ('ok', 'pass', 'rejected', 'error', 'skipped')


def test_ticker_outside_allowed_is_error_and_recorded():
    rec = Recorder()
    env = call(ctx(), rec, 'get_price_bars', {'ticker': 'NVDA', 'lookback': 60}, now=CUTOFF)
    assert env['ref'] == 'call:1' and env['error']
    assert len(rec.calls) == 1
    assert rec.calls[0]['tool'] == 'get_price_bars'
    assert rec.calls[0]['result'] == env
    assert rec.calls[0]['result_sha256'] == result_hash(env)


def test_lookback_out_of_range_is_error():
    for bad in (0, 1000):
        with pytest.raises(ToolError):
            get_price_bars(ctx(), 'AMD', lookback=bad)
    assert MAX_LOOKBACK == 120
    assert PRICE_TOOLS == frozenset({'get_price_bars', 'get_indicators'})


def test_trigger_has_no_plan_levels():
    rows = [{'id': 1, 'ticker': 'AMD', 'signal_date': S, 'action': 'buy', 'rule_id': 'trend_reclaim',
             'rule_version': '1', 'inputs': {'close': 110, 'ma20': 108}, 'run_id': None, 'created_at': CUTOFF,
             'entry_low': '99.5', 'entry_high': '101.5', 'stop': '96', 'target1': '108'}]
    out = get_trigger(ctx(read=lambda fn: rows))
    assert out == [{'rule_id': 'trend_reclaim', 'action': 'buy', 'inputs': {'close': 110, 'ma20': 108}}]


def test_seq_increments_across_phases():
    rec = Recorder()
    r1 = rec.record('get_news', {}, data={'a': 1}, now=CUTOFF)
    r2 = rec.record('get_price_bars', {'ticker': 'AMD'}, data={'bars': []}, now=CUTOFF)
    rec.phase = 'counter'
    r3 = rec.record('get_news', {}, data={'b': 2}, now=CUTOFF)
    assert [r['ref'] for r in (r1, r2, r3)] == ['call:1', 'call:2', 'call:3']
    assert rec.refs('propose') == {'call:1', 'call:2'}
    assert rec.refs('counter') == {'call:3'}
    assert rec.refs() == {'call:1', 'call:2', 'call:3'}
    assert [c['seq'] for c in rec.calls] == [1, 2, 3]
    assert [c['phase'] for c in rec.calls] == ['propose', 'propose', 'counter']


def test_concurrent_records_get_distinct_monotonic_seqs():
    """ToolNode runs a round's parallel tool calls on a thread pool; two records that interleave must still
    get distinct ``call:N`` (MU 2026-10-28 attempt 2 and LIN 2026-10-30 both wrote two calls with one seq
    and the unit died on the ``(run_id, seq)`` unique key)."""
    import threading

    barrier = threading.Barrier(2)

    class RendezvousList(list):
        """``len()`` waits for the other recorder thread, so an unguarded ``len + 1`` numbering
        hands both threads the same number; a guarded one times the barrier out and proceeds."""
        def __len__(self):
            n = super().__len__()
            try:
                barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return n

    rec = Recorder()
    rec.calls = RendezvousList()
    envelopes: list[dict] = []
    errors: list[BaseException] = []

    def worker(tool):
        try:
            envelopes.append(rec.record(tool, {}, data={'x': tool}, now=CUTOFF))
        except BaseException as exc:   # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ('get_indicators', 'get_news')]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not errors
    assert sorted(e['ref'] for e in envelopes) == ['call:1', 'call:2']
    assert sorted(c['seq'] for c in rec.calls) == [1, 2]
    assert [c['result']['ref'] for c in rec.calls] == [f"call:{c['seq']}" for c in rec.calls]


def test_price_refs_exclude_errors():
    rec = Recorder()
    rec.record('get_price_bars', {}, data={'bars': []}, now=CUTOFF)
    rec.record('get_indicators', {}, error='boom', now=CUTOFF)
    rec.record('get_news', {}, data={'items': []}, now=CUTOFF)
    assert rec.price_refs() == {'call:1'}


def test_hash_is_canonical():
    assert result_hash({'a': 1, 'b': 2}) == result_hash({'b': 2, 'a': 1})
    assert len(result_hash({'a': 1})) == 64


def _bars(closes, volume=1000):
    days = [add_trading_days(S, -(len(closes) - 1 - i)) for i in range(len(closes))]
    return [{'price_date': d, 'open': c, 'high': c * 1.02, 'low': c * 0.98, 'close': c, 'adj_close': c,
             'volume': volume, 'split_factor': 1.0} for d, c in zip(days, closes)]


def test_indicators_short_history_nulls():
    out = indicators(_bars([100 + i for i in range(120)]), _bars([500] * 120))
    assert out['ma200'] is None and out['high_252'] is None and out['low_252'] is None
    assert out['ma50'] is not None
    assert out['rs20'] is not None  # 21 根即可
    assert out['rs60'] is not None  # 61 根即够，120 根充足


def test_indicators_values():
    ticker = _bars([100 + i for i in range(300)], volume=1000)
    ticker[-1]['volume'] = 3000
    spy = _bars([500.0] * 300)
    out = indicators(ticker, spy)
    assert out['last_close'] == 399
    assert out['ma20'] == pytest.approx(sum(range(380, 400)) / 20)
    assert out['volume_ratio'] == pytest.approx(3000 / 1100)  # 均量含当日：19×1000+3000 / 20
    assert out['rs20'] == pytest.approx(399 / 379 - 1)  # 21 根窗口，SPY 20 日收益为 0


def _bar_row(conn, **overrides):
    values = {'ticker': 'NVDA', 'price_date': date(2026, 10, 20), 'open': 100, 'high': 105, 'low': 99,
              'close': 100, 'volume': 1000, 'adj_close': 100, 'revision': 1, 'fetched_at': CUTOFF - timedelta(hours=2)}
    values.update(overrides)
    cols = ', '.join(values)
    marks = ', '.join(['%s'] * len(values))
    conn.execute(f'INSERT INTO price_bars ({cols}) VALUES ({marks})', list(values.values()))


def _news_row(conn, title, *, fetched_at):
    row = conn.execute("INSERT INTO news_items (source, title, url, publisher, published_at, fetched_at, "
                       "content_hash) VALUES ('yfinance', %s, %s, 'p', %s, %s, %s) RETURNING id",
                       [title, f'https://n.example/{title}', CUTOFF - timedelta(days=1), fetched_at,
                        f'hash-{title}']).fetchone()
    conn.execute('INSERT INTO news_item_tickers (news_item_id, ticker) VALUES (%s, %s)', [row[0], 'NVDA'])


def test_bars_after_cutoff_invisible(workbench_db):
    before = CUTOFF - timedelta(hours=2)
    after = CUTOFF + timedelta(hours=2)
    _bar_row(workbench_db, price_date=date(2026, 10, 20), close=100, fetched_at=before)
    _bar_row(workbench_db, price_date=date(2026, 10, 21), close=101, fetched_at=before)
    _bar_row(workbench_db, price_date=date(2026, 10, 20), close=999, revision=2, fetched_at=after)  # cutoff 后的修订
    _bar_row(workbench_db, price_date=date(2026, 10, 21), close=888, revision=2, fetched_at=after)  # cutoff 后的修订
    _bar_row(workbench_db, price_date=date(2026, 10, 22), close=777, fetched_at=after)              # cutoff 后才抓到的 K 线
    _news_row(workbench_db, 'before', fetched_at=before)
    _news_row(workbench_db, 'after', fetched_at=after)

    sql_ctx = ctx('NVDA', read=lambda fn: fn(workbench_db))
    from investment_assistant.analysis.agent.tools import get_news
    assert [(b['date'], b['close']) for b in get_price_bars(sql_ctx, 'NVDA', lookback=10)] \
        == [('2026-10-20', 100.0), ('2026-10-21', 101.0)]
    assert [n['title'] for n in get_news(sql_ctx, days=7)] == ['before']


# ---------------------------------------------------------------- get_concept (v2.6 spec §10)

import hashlib   # noqa: E402

import yaml   # noqa: E402

from investment_assistant.core import knowledge   # noqa: E402
from investment_assistant.analysis.agent import tools as agent_tools   # noqa: E402

CONCEPT_DESCRIPTION = ('读取系统概念卡：不给 id 返回全部 id 与标题；给 id（如 `plan.stop`）返回整张卡。'
                       '概念卡随代码版本走，不受截止时间约束')


def _card(root, card_id, *, title, summary='参考价减 2 倍 ATR14。', body='## 是什么\n\n止损是一个价位。\n'):
    folder = root / 'concepts'
    folder.mkdir(parents=True, exist_ok=True)
    meta = {'id': card_id, 'title': title, 'group': card_id.split('.')[0], 'version': 2, 'applies_to': ['plan-1'],
            'summary': summary, 'related': []}
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=None)
    (folder / f'{card_id}.md').write_text(f'---\n{front}---\n\n{body}', encoding='utf-8')


@pytest.fixture
def cards(tmp_path, monkeypatch):
    _card(tmp_path, 'plan.stop', title='止损')
    _card(tmp_path, 'indicator.atr14', title='ATR14', summary='14 日平均真实波幅。')
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path)
    knowledge.reset_cache()
    yield tmp_path
    knowledge.reset_cache()


def boom_ctx():
    def read(fn):
        raise AssertionError('get_concept must not read the database')
    return ToolContext(ticker='AMD', session=S, cutoff_at=CUTOFF, read=read)


def test_tools_count_is_ten():
    assert len(agent_tools.TOOLS) == 10 and list(agent_tools.TOOLS)[-1] == 'get_knowledge'
    assert list(agent_tools.TOOLS).index('get_concept') == list(agent_tools.TOOLS).index('get_knowledge') - 1
    assert set(agent_tools.TOOLS) == set(agent_tools.TOOL_DESCRIPTIONS)
    assert agent_tools.TOOL_DESCRIPTIONS['get_concept'] == CONCEPT_DESCRIPTION
    assert agent_tools.TOOL_DESCRIPTIONS['get_knowledge'] == (
        '读用户审定过的经验（我的知识）：给 ticker 或概念 id 取挂在上面的条目，不给取索引；'
        '只返回截止时间之前写入的版本；引用写 know:<id>@<版本>')
    assert list(agent_tools.TOOLS)[:8] == ['get_trigger', 'get_price_bars', 'get_indicators', 'get_filings',
                                           'get_news', 'get_earnings', 'get_market_context', 'get_position']


def test_get_concept_index_and_card(cards):
    rec = Recorder()
    index = call(boom_ctx(), rec, 'get_concept', {}, now=CUTOFF)['data']
    assert index['knowledge_version'] == 'knowledge-1' and index['point_in_time'] is False
    assert index['concepts'] == [{'id': 'indicator.atr14', 'title': 'ATR14', 'group': 'indicator'},
                                 {'id': 'plan.stop', 'title': '止损', 'group': 'plan'}]
    card = call(boom_ctx(), rec, 'get_concept', {'concept_id': 'plan.stop'}, now=CUTOFF)['data']
    assert card['concept_ref'] == 'concept:plan.stop@2' and card['id'] == 'plan.stop' and card['title'] == '止损'
    assert card['group'] == 'plan' and card['version'] == 2 and card['applies_to'] == ['plan-1']
    assert card['summary'] == '参考价减 2 倍 ATR14。' and '止损是一个价位' in card['body']
    assert card['knowledge_version'] == 'knowledge-1'
    assert set(card) == {'concept_ref', 'id', 'title', 'group', 'version', 'applies_to', 'summary', 'body',
                         'content_sha256', 'knowledge_version', 'point_in_time', 'note'}
    assert [c['seq'] for c in rec.calls] == [1, 2] and rec.calls[1]['args'] == {'concept_id': 'plan.stop'}
    assert rec.calls[1]['result'] == {'ref': 'call:2', 'data': card}


def test_get_concept_never_reads_db_and_marks_exemption(cards):
    rec = Recorder()
    env = call(boom_ctx(), rec, 'get_concept', {'concept_id': 'plan.stop'}, now=CUTOFF)
    data = env['data']
    assert data['point_in_time'] is False and data['note'] == '概念卡随代码版本走，不按截止时间回读'
    assert data['concept_ref'] == 'concept:plan.stop@2'
    assert data['content_sha256'] == hashlib.sha256('## 是什么\n\n止损是一个价位。'.encode('utf-8')).hexdigest()
    # the exemption is in the stored record, not only in the live result
    stored = rec.calls[0]
    assert stored['tool'] == 'get_concept' and stored['result']['data']['point_in_time'] is False
    assert stored['result_sha256'] == result_hash(stored['result'])
    # the cutoff is irrelevant: the same call under a much earlier cutoff returns the same card
    early = ToolContext(ticker='AMD', session=S, cutoff_at=datetime(2020, 1, 1, tzinfo=UTC), read=boom_ctx().read)
    assert agent_tools.get_concept(early, 'plan.stop') == data


def test_get_concept_unknown_id_error_envelope(cards):
    rec = Recorder()
    env = call(boom_ctx(), rec, 'get_concept', {'concept_id': 'plan.nope'}, now=CUTOFF)
    assert env == {'ref': 'call:1', 'error': '未知概念 plan.nope，不给 id 可取全部列表'}
    assert call(boom_ctx(), rec, 'get_concept', {'concept_id': 'foo'}, now=CUTOFF)['error'] \
        == '未知概念 foo，不给 id 可取全部列表'
    assert 'error' in call(boom_ctx(), rec, 'get_concept', {'concept_id': 5}, now=CUTOFF)
    assert rec.calls[0]['result'] == env and rec.calls[0]['result_sha256'] == result_hash(env)


def test_get_concept_when_catalog_broken_records_error_envelope(tmp_path, monkeypatch):
    _card(tmp_path, 'plan.stop', title='止损')
    (tmp_path / 'concepts' / 'plan.stop.md').write_text('no frontmatter at all', encoding='utf-8')
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path)
    knowledge.reset_cache()
    rec = Recorder()
    env = call(boom_ctx(), rec, 'get_concept', {}, now=CUTOFF)
    assert env['ref'] == 'call:1' and env['error'].startswith('概念卡不可用：')
    assert 'data' not in env and rec.calls[0]['result'] == env
    # a failed load is not cached: once the card is fixed the very next call works
    _card(tmp_path, 'plan.stop', title='止损')
    assert call(boom_ctx(), rec, 'get_concept', {}, now=CUTOFF)['data']['concepts'][0]['id'] == 'plan.stop'
    knowledge.reset_cache()


def test_get_concept_with_no_cards_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path / 'absent')
    knowledge.reset_cache()
    rec = Recorder()
    assert call(boom_ctx(), rec, 'get_concept', {}, now=CUTOFF)['data']['concepts'] == []
    assert call(boom_ctx(), rec, 'get_concept', {'concept_id': 'plan.stop'}, now=CUTOFF)['error'].startswith('未知概念')
    knowledge.reset_cache()


def test_get_concept_not_a_price_tool(cards):
    assert 'get_concept' not in PRICE_TOOLS
    rec = Recorder()
    call(boom_ctx(), rec, 'get_concept', {'concept_id': 'plan.stop'}, now=CUTOFF)
    assert rec.refs() == {'call:1'} and rec.price_refs() == set()


# ---------------------------------------------------------------- get_knowledge (v2.7 spec §6.1)

import inspect   # noqa: E402
import re   # noqa: E402

# Repository consumers use their owning layers below.

KNOWLEDGE_NOTE_TEXT = '这是用户审定过的经验，是判断不是事实；价位不能只引用它'


def _version(kid, version, *, created_at=None, title=None, tickers=('AMD',), concepts=()):
    return {'id': kid * 10 + version, 'knowledge_id': kid, 'version': version, 'supersedes_id': None,
            'request_id': None, 'request_hash': 'h', 'draft_id': None, 'title': title or f'经验{kid}',
            'body': f'正文{kid}.{version}', 'scope': '范围', 'counter_examples': '反例', 'concepts': list(concepts),
            'tickers': list(tickers), 'schema_version': 'my-knowledge-1',
            'created_at': created_at or CUTOFF - timedelta(days=kid, hours=version)}


def knowledge_ctx(monkeypatch, versions=(), statuses=(), ready=True):
    """A tool context whose knowledge reads are faked; returns (ctx, the kwargs knowledge_rows was called with)."""
    seen: dict = {}
    monkeypatch.setattr(core_schema, 'growth_ready', lambda conn: ready)

    def rows(conn, **kwargs):
        seen.update(kwargs)
        return list(versions), list(statuses)
    monkeypatch.setattr(data_repo, 'knowledge_rows', rows)
    return ToolContext(ticker='AMD', session=S, cutoff_at=CUTOFF, read=lambda fn: fn(object())), seen


def test_get_knowledge_args(monkeypatch, cards):
    ctx_, seen = knowledge_ctx(monkeypatch, [_version(1, 1)])
    rec = Recorder()
    both = call(ctx_, rec, 'get_knowledge', {'ticker': 'AMD', 'concept': 'plan.stop'}, now=CUTOFF)
    assert both == {'ref': 'call:1', 'error': 'ticker 与 concept 只能给一个'}
    assert 'error' in call(ctx_, rec, 'get_knowledge', {'ticker': 'NVDA'}, now=CUTOFF)         # out of unit
    assert call(ctx_, rec, 'get_knowledge', {'concept': 'plan.nope'}, now=CUTOFF)['error'] == '未知概念 plan.nope'
    assert call(ctx_, rec, 'get_knowledge', {'concept': 'foo'}, now=CUTOFF)['error'] == '未知概念 foo'
    assert seen == {}                                                                          # no read for a bad call
    assert 'data' in call(ctx_, rec, 'get_knowledge', {'ticker': 'spy'}, now=CUTOFF)           # the allowed extras
    assert seen['ticker'] == 'SPY' and seen['concept'] is None and seen['cutoff'] == CUTOFF
    # a concept as the card ref: the version is ignored, matched by id
    env = call(ctx_, rec, 'get_knowledge', {'concept': 'concept:plan.stop@1'}, now=CUTOFF)
    assert env['data']['query'] == {'concept': 'plan.stop'} and seen['concept'] == 'plan.stop'
    assert seen['ticker'] is None


def test_get_knowledge_concept_with_unreadable_catalog_checks_format_only(monkeypatch, tmp_path):
    (tmp_path / 'concepts').mkdir()
    (tmp_path / 'concepts' / 'plan.stop.md').write_text('no frontmatter', encoding='utf-8')
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path)
    knowledge.reset_cache()
    ctx_, seen = knowledge_ctx(monkeypatch, [])
    rec = Recorder()
    assert 'data' in call(ctx_, rec, 'get_knowledge', {'concept': 'plan.anything'}, now=CUTOFF)
    assert seen['concept'] == 'plan.anything'
    assert call(ctx_, rec, 'get_knowledge', {'concept': 'foo'}, now=CUTOFF)['error'] == '未知概念 foo'
    knowledge.reset_cache()


def test_get_knowledge_shape(monkeypatch):
    versions = [_version(k, 1) for k in range(1, 13)] + [_version(3, 2, title='经验3新'),
                                                         _version(13, 1, tickers=('SPY',))]
    ctx_, _ = knowledge_ctx(monkeypatch, versions)
    rec = Recorder()
    data = call(ctx_, rec, 'get_knowledge', {'ticker': 'AMD'}, now=CUTOFF)['data']
    assert data['query'] == {'ticker': 'AMD'} and data['point_in_time'] is True
    assert data['cutoff_at'] == CUTOFF.isoformat() and data['note'] == KNOWLEDGE_NOTE_TEXT
    assert len(data['entries']) == 10 and data['omitted'] == 3               # 13 items visible, at most 10 shown
    refs = [e['ref'] for e in data['entries']]
    assert all(re.fullmatch(r'know:[1-9][0-9]*@[1-9][0-9]*', r) for r in refs)
    assert 'know:3@2' in refs and 'know:3@1' not in refs                     # the highest visible version only
    first = data['entries'][0]
    assert set(first) == {'ref', 'id', 'version', 'title', 'body', 'scope', 'counter_examples', 'concepts',
                          'tickers', 'written_at'}
    assert refs[0] == 'know:1@1' and first['id'] == 1 and first['written_at'] == versions[0]['created_at'].isoformat()
    written = [e['written_at'] for e in data['entries']]
    assert written == sorted(written, reverse=True)                          # newest first
    assert rec.knowledge_refs() == set(refs)


def test_get_knowledge_index_has_no_bodies(monkeypatch):
    ctx_, seen = knowledge_ctx(monkeypatch, [_version(k, 1, concepts=['concept:plan.stop@1']) for k in range(1, 61)])
    data = call(ctx_, Recorder(), 'get_knowledge', {}, now=CUTOFF)['data']
    assert data['query'] == {} and seen['ticker'] is None and seen['concept'] is None
    assert len(data['entries']) == 50 and data['omitted'] == 10              # the index holds at most 50
    for entry in data['entries']:
        assert set(entry) == {'ref', 'title', 'concepts', 'tickers', 'written_at'}
    assert 'body' not in json.dumps(data, ensure_ascii=False)


def test_get_knowledge_hides_archived_and_later_versions(monkeypatch):
    archived = {'id': 1, 'knowledge_id': 2, 'status': 'archived', 'reason': '', 'created_at': CUTOFF - timedelta(hours=1)}
    later = {**archived, 'id': 2, 'knowledge_id': 1, 'created_at': CUTOFF + timedelta(hours=1)}
    versions = [_version(1, 1), _version(2, 1), _version(3, 1, created_at=CUTOFF + timedelta(minutes=1))]
    ctx_, _ = knowledge_ctx(monkeypatch, versions, [archived, later])
    refs = {e['ref'] for e in call(ctx_, Recorder(), 'get_knowledge', {'ticker': 'AMD'}, now=CUTOFF)['data']['entries']}
    assert refs == {'know:1@1'}                  # 2 archived before the cutoff, 3 written after it, 1's archive is later


def test_get_knowledge_without_migration(monkeypatch):
    ctx_, seen = knowledge_ctx(monkeypatch, [_version(1, 1)], ready=False)
    rec = Recorder()
    env = call(ctx_, rec, 'get_knowledge', {}, now=CUTOFF)
    assert env == {'ref': 'call:1', 'error': '我的知识不可用：迁移 027 未执行'} and seen == {}
    assert rec.knowledge_refs() == set()


def test_get_knowledge_not_a_price_tool(monkeypatch):
    assert 'get_knowledge' not in PRICE_TOOLS
    ctx_, _ = knowledge_ctx(monkeypatch, [_version(1, 1)])
    rec = Recorder()
    call(ctx_, rec, 'get_knowledge', {'ticker': 'AMD'}, now=CUTOFF)
    assert rec.refs() == {'call:1'} and rec.price_refs() == set()


def test_recorder_knowledge_refs_by_phase():
    rec = Recorder()
    entries = lambda *refs: {'entries': [{'ref': r} for r in refs]}                         # noqa: E731
    rec.record('get_knowledge', {}, data=entries('know:1@1', 'know:2@3'), now=CUTOFF)
    rec.record('get_news', {}, data=[{'ref': 'know:9@9'}], now=CUTOFF)                     # another tool: ignored
    rec.record('get_knowledge', {'ticker': 'X'}, error='boom', now=CUTOFF)                  # failed call: ignored
    rec.phase = 'counter'
    rec.record('get_knowledge', {}, data=entries('know:2@3', 'know:4@1'), now=CUTOFF)
    assert rec.knowledge_refs() == {'know:1@1', 'know:2@3', 'know:4@1'}
    assert rec.knowledge_refs('propose') == {'know:1@1', 'know:2@3'}
    assert rec.knowledge_refs('counter') == {'know:2@3', 'know:4@1'}
    assert rec.knowledge_refs('review') == set()


def test_call_dispatches_in_the_given_table_only():
    seen = []
    table = {'only_here': lambda ctx_, x=1: seen.append(x) or {'x': x}}
    rec = Recorder()
    assert call(ctx(), rec, 'only_here', {'x': 3}, now=CUTOFF, tools=table)['data'] == {'x': 3}
    assert call(ctx(), rec, 'only_here', {}, now=CUTOFF)['error'] == '未知工具 only_here'         # default table: TOOLS
    assert seen == [3]


def test_no_tool_queries_draft_tables():
    """Nothing the agent can call reads the lesson drafts or the card drafts (spec §5.4, D11)."""
    import investment_assistant.views.derivation as derivation_view
    import investment_assistant.views.reasoning as reasoning
    import investment_assistant.core.tx as service
    from investment_assistant.execution import simulation as plan_shadow
    from investment_assistant.review import discuss_tools

    banned = ('knowledge_drafts', 'review_card_drafts')
    modules = (agent_tools, discuss_tools)
    extra = [execution_chains.chain_status, execution_chains.unit_buys, derivation_view.load_rows, reasoning.view_in,
             reasoning._inputs, plan_shadow.simulate]
    sources = [inspect.getsource(m) for m in modules] + [inspect.getsource(f) for f in extra]
    repositories = dict(data_repo=data_repo, execution_repo=execution_repo, execution_repo_legacy=execution_repo_legacy,
                        analysis_repo=analysis_repo, review_repo=review_repo, core_schema=core_schema)
    called = {(module, name) for source in sources
              for module, name in re.findall(r'(\w+)\.(\w+)', source)
              if module in repositories and callable(getattr(repositories[module], name, None))}
    assert {'knowledge_rows', 'growth_ready', 'review_card', 'card_verdict', 'knowledge_item'} <= {n for _, n in called}
    for module, name in called:
        source = inspect.getsource(getattr(repositories[module], name))
        assert not any(word in source for word in banned), name
    for source in sources:
        assert not any(word in source for word in banned)


# --- SQL: the cutoff and the drafts (spec §16.1) ---

from test_workbench_repository_growth import card as make_card, item_with_versions   # noqa: E402


def _archive(conn, item_id, status, created_at):
    sid = review_repo.insert_knowledge_status(conn, {'knowledge_id': item_id, 'status': status, 'reason': '',
                                                    'request_id': uuid.uuid4(), 'request_hash': 'h'})
    conn.execute('UPDATE knowledge_status SET created_at = %s WHERE id = %s', (created_at, sid))


def test_get_knowledge_respects_cutoff(workbench_db):
    conn = workbench_db
    before, after = CUTOFF - timedelta(hours=2), CUTOFF + timedelta(hours=1)
    concept = ['concept:plan.stop@1']
    a = item_with_versions(conn, [(before, ['AMD'], concept), (after, ['AMD'], concept)])   # v2 written after
    b = item_with_versions(conn, [(before, ['AMD'], concept)])
    _archive(conn, b, 'archived', after)                                                   # archived after
    c = item_with_versions(conn, [(after, ['AMD'], concept)])                              # created after
    d = item_with_versions(conn, [(before, ['AMD'], concept)])
    _archive(conn, d, 'archived', before)                                                  # archived before
    e = item_with_versions(conn, [(before, ['AMD'], concept)])
    _archive(conn, e, 'archived', before)
    _archive(conn, e, 'active', after)                                                     # restored after
    f = item_with_versions(conn, [(before, ['AMD'], ['concept:plan.stop@1']),
                                  (before + timedelta(minutes=5), ['MSFT'], [])])          # moved off the ticker
    ctx_ = ctx('AMD', read=lambda fn: fn(conn))
    want = {f'know:{a}@1', f'know:{b}@1'}
    by_ticker = get_knowledge_data(ctx_, ticker='AMD')
    assert {x['ref'] for x in by_ticker['entries']} == want
    assert {x['ref'] for x in get_knowledge_data(ctx_, concept='plan.stop')['entries']} == want
    index = get_knowledge_data(ctx_)
    assert {x['ref'] for x in index['entries']} == want | {f'know:{f}@2'}
    assert by_ticker['point_in_time'] is True and by_ticker['cutoff_at'] == CUTOFF.isoformat()
    assert all(x['title'] and 'body' not in x for x in index['entries'])
    # the same call at a later cutoff sees the rest
    later = ToolContext(ticker='AMD', session=S, cutoff_at=CUTOFF + timedelta(days=1), read=lambda fn: fn(conn))
    assert {x['ref'] for x in agent_tools.get_knowledge(later, ticker='AMD')['entries']} == {
        f'know:{a}@2', f'know:{c}@1', f'know:{e}@1'}


def get_knowledge_data(ctx_, **kwargs):
    return call(ctx_, Recorder(), 'get_knowledge', kwargs, now=CUTOFF)['data']


def test_get_knowledge_never_reads_drafts(workbench_db):
    conn = workbench_db
    run = uuid.uuid4()
    cid = make_card(conn, ticker='AMD')
    conn.execute(
        "INSERT INTO agent_runs (id, ticker, signal_date, pool, attempt, mode, agent_version, provider, model, "
        "cutoff_at, status, reason, started_at, finished_at) VALUES (%s, 'AMD', %s, 'main', 1, 'retro', "
        "'agent-retro-1', 'chatgpt', 'gpt-test', %s, 'ok', NULL, %s, %s)", (run, S, CUTOFF, CUTOFF, CUTOFF))
    draft = review_repo.insert_card_draft(conn, {'card_id': cid, 'attempt': 1, 'trigger': 'batch', 'agent_run_id': run,
                                                'status': 'ok', 'reason': None, 'output': {'headline': '草稿'}})
    base = {'origin': 'review_card', 'card_draft_id': draft, 'discussion_turn_id': None, 'target_knowledge_id': None,
            'target_version': None, 'body': 'b', 'scope': 's', 'counter_examples': 'c', 'concepts': [],
            'tickers': ['AMD'], 'refs': ['fact:plans']}
    ids = [review_repo.insert_knowledge_draft(conn, {**base, 'lesson_index': i, 'title': f'草稿{i}'}) for i in range(3)]
    review_repo.insert_draft_verdict(conn, {'draft_id': ids[1], 'action': 'dismiss', 'knowledge_version_id': None,
                                           'request_id': uuid.uuid4()})                  # a dismissed one
    discussion = review_repo.insert_discussion(conn, {'request_id': uuid.uuid4(), 'request_hash': 'h',
                                                     'context_kind': 'card', 'ticker': 'AMD', 'signal_date': S,
                                                     'plan_id': None, 'card_id': cid})
    user = review_repo.insert_turn(conn, {'discussion_id': discussion['id'], 'seq': 1, 'role': 'user',
                                         'request_id': uuid.uuid4(), 'request_hash': 'h', 'body': '问'})
    answer = review_repo.insert_turn(conn, {'discussion_id': discussion['id'], 'seq': 2, 'role': 'assistant',
                                           'body': '答', 'reply_to': user['id'], 'status': 'ok', 'cutoff_at': CUTOFF})
    review_repo.insert_knowledge_draft(conn, {'origin': 'discussion', 'discussion_turn_id': answer['id'],
                                             'title': '讨论草稿', 'body': 'b', 'scope': 's', 'counter_examples': 'c',
                                             'concepts': [], 'tickers': ['AMD'], 'refs': ['fact:context'],
                                             'card_draft_id': None, 'lesson_index': None, 'target_knowledge_id': None,
                                             'target_version': None})
    ctx_ = ctx('AMD', read=lambda fn: fn(conn))
    assert get_knowledge_data(ctx_, ticker='AMD')['entries'] == []
    assert get_knowledge_data(ctx_)['entries'] == []
    blob = json.dumps([get_knowledge_data(ctx_, ticker='AMD'), get_knowledge_data(ctx_)], ensure_ascii=False)
    assert '草稿' not in blob and '讨论草稿' not in blob
    # a reviewed lesson written as knowledge is visible as soon as it exists before the cutoff
    item_with_versions(conn, [(CUTOFF - timedelta(hours=1), ['AMD'], [])])
    assert len(get_knowledge_data(ctx_, ticker='AMD')['entries']) == 1


def test_get_position_respects_cutoff(monkeypatch):
    """v2.9 D33: only fills executed and recorded by the cutoff count; a void recorded after it is not applied."""
    # Repository consumers use their owning layers below.
    from investment_assistant.analysis.agent.tools import get_position

    def trade(trade_id, qty, executed, recorded, voided=None, side='buy'):
        return {'id': trade_id, 'ticker': 'AMD', 'side': side, 'quantity': qty, 'price': 100, 'fee': 0,
                'executed_at': executed, 'recorded_at': recorded, 'voided_at': voided}

    before = CUTOFF - timedelta(days=2)
    after = CUTOFF + timedelta(hours=3)
    rows = [trade('a', 10, before, before), trade('late', 5, before, after),
            trade('later', 7, after, after), trade('voided', 3, before, before, voided=after)]
    asked = []

    def list_trades(conn, ticker=None, *, cutoff=None):
        asked.append(cutoff)
        out = []
        for t in rows:
            if cutoff is not None and (t['recorded_at'] > cutoff or t['executed_at'] > cutoff):
                continue
            out.append({**t, 'voided_at': t['voided_at'] if t['voided_at'] and t['voided_at'] <= cutoff else None}
                       if cutoff is not None else t)
        return out

    monkeypatch.setattr(execution_repo, 'list_trades', list_trades)
    monkeypatch.setattr(data_repo, 'split_events', lambda conn, ticker: [])
    position = get_position(ctx(read=lambda fn: fn(object())))
    assert asked == [CUTOFF]
    assert position['quantity'] == '13' or float(position['quantity']) == 13       # 10 + the not-yet-voided 3
