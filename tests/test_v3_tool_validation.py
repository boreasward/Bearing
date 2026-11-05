"""toolsurface.validation: ticker normalization, brief/chat scope, agent-level guard rails."""
from decimal import Decimal as D

import pytest

from investment_assistant.toolsurface.validation import TICKER_RE, check_agent_levels, in_scope, normalize_ticker


def test_normalize_ticker():
    assert normalize_ticker(' aapl ') == 'AAPL' and normalize_ticker('BRK.B') == 'BRK.B'
    for bad in ('', 'AA PL', '1ABC', 'TOOLONGTICKER', None, 123):
        with pytest.raises(ValueError, match='invalid_ticker'):
            normalize_ticker(bad)


def test_ticker_re_matches_the_shapes_the_tool_accepts():
    assert TICKER_RE.match('AAPL') and TICKER_RE.match('BRK.B') and TICKER_RE.match('BF-B')
    assert not TICKER_RE.match('aapl') and not TICKER_RE.match('A PL') and not TICKER_RE.match('1A')


def test_in_scope():
    assert in_scope('brief', 'AAPL', ['AAPL']) and not in_scope('brief', 'MSFT', ['AAPL'])
    assert not in_scope('brief', 'AAPL', []) and in_scope('chat', 'MSFT', [])


def test_check_agent_levels():
    # last_close 100、ATR 2。主体价位 entry_low 99 / entry_high 101 / stop 96 / target1 107 -> []
    good = {'entry_low': D(99), 'entry_high': D(101), 'stop': D(96), 'target1': D(107)}
    assert check_agent_levels(good, last_close=D(100), atr=D(2)) == []
    # 顺序错（stop >= entry_low）-> 只报 ['unordered']
    unordered = {**good, 'stop': D(99)}
    assert check_agent_levels(unordered, last_close=D(100), atr=D(2)) == ['unordered']
    # entry_high 抬过上限（target1 同步抬高保住 1R 回报）-> 'entry_too_high'
    too_high = {**good, 'entry_high': D('102.01'), 'target1': D(109)}
    assert check_agent_levels(too_high, last_close=D(100), atr=D(2)) == ['entry_too_high']
    # entry_low 压到下限之外（stop 随之下移保住顺序与止损距离）-> 'entry_too_low'
    too_low = {**good, 'entry_low': D('95.99'), 'stop': D(93), 'target1': D(109)}
    assert check_agent_levels(too_low, last_close=D(100), atr=D(2)) == ['entry_too_low']
    # stop 距 entry_low 不足 1（0.5 ATR）-> 'stop_too_tight'；超过 6（3 ATR）-> 'stop_too_wide'
    tight = {**good, 'stop': D('98.5')}
    assert check_agent_levels(tight, last_close=D(100), atr=D(2)) == ['stop_too_tight']
    wide = {**good, 'stop': D(92), 'target1': D(110)}
    assert check_agent_levels(wide, last_close=D(100), atr=D(2)) == ['stop_too_wide']
    # target1 - entry_high 小于 entry_high - stop -> 'reward_below_1r'
    thin_reward = {**good, 'target1': D('102.5')}
    assert check_agent_levels(thin_reward, last_close=D(100), atr=D(2)) == ['reward_below_1r']
    # 多条同时违反时一并给出（target1 同步抬高保住 1R，只留两条违反）
    both = {**good, 'entry_high': D(103), 'stop': D(92), 'target1': D(114)}
    assert check_agent_levels(both, last_close=D(100), atr=D(2)) == ['entry_too_high', 'stop_too_wide']
