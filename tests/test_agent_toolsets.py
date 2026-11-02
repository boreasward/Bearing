"""Versioned role capabilities: names, dispatch boundaries and frozen host ports."""
import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from investment_assistant.analysis.agent import graph, toolsets
from investment_assistant.analysis.agent.tools import Recorder, ToolContext, ToolError, call

NOW = datetime(2026, 10, 30, 22, tzinfo=UTC)
EXPECTED = {
    'propose@agent-4': 'c80a16a979906f4623f8dad2dc73975ce592556dc063f781e597d45955f53cac',
    'counter@agent-4': 'c80a16a979906f4623f8dad2dc73975ce592556dc063f781e597d45955f53cac',
    'research@agent-4': '2998db72096759d5843010a276e8d7587b2aa5e8d02729b5543a620aab9ee2aa',
    'review@agent-review-2': 'fe0018df8822748bc02b5557e95890ba7826e141310d59e58d1183d1431258cd',
    'portfolio@agent-portfolio-2': 'f27d7033b6fbf45f15c0eff37636212f706969a0544a4f9b4be472d280c5a468',
    'pm@agent-pm-1': '0bc3fe49cd86915b5fea1713977f22f74402efc11722b32dbfbdf89cf20eafeb',
    'optimize@agent-optimize-1': 'c0aecf31c5b8cad094bdd76515417210a91c406486a6c603298fb3e4ee513fdd',
    'brief@agent-impl-brief-1': 'c0aecf31c5b8cad094bdd76515417210a91c406486a6c603298fb3e4ee513fdd',
    'retro@agent-retro-2': 'fe0018df8822748bc02b5557e95890ba7826e141310d59e58d1183d1431258cd',
    'discuss@agent-discuss-1': 'b32360fc4ff784ec0129a7a1dc34dafb4f7e14cec0129d117c7b77351f1de723',
}


def context(**kwargs):
    return ToolContext('AMD', date(2026, 10, 30), NOW, lambda fn: fn(None), **kwargs)


def test_keys_and_fingerprints_locked():
    assert toolsets.FINGERPRINTS == EXPECTED
    assert set(toolsets.TOOLSETS) == set(EXPECTED)
    for key, names in toolsets.TOOLSETS.items():
        assert isinstance(names, frozenset)
        assert hashlib.sha256('\n'.join(sorted(names)).encode()).hexdigest() == EXPECTED[key]
        assert toolsets.fingerprint(key) == EXPECTED[key]
        assert set(toolsets.tools_for(key)) == names
    with pytest.raises(KeyError):
        toolsets.tools_for('propose@unknown')


def test_research_toolset_has_no_price_tools():
    assert not {'get_price_bars', 'get_indicators', 'get_indicator', 'get_market_context'} & toolsets.TOOLSETS['research@agent-4']
    assert 'get_research_dossier' in toolsets.TOOLSETS['research@agent-4']
    assert {k for k, names in toolsets.TOOLSETS.items() if 'get_roadmap' in names} == {
        'optimize@agent-optimize-1', 'brief@agent-impl-brief-1', 'discuss@agent-discuss-1'}


def test_call_rejects_tool_outside_toolset():
    invoked = []
    ctx = context(bindings={'get_roadmap': lambda *a, **kw: invoked.append(kw)})
    recorder = Recorder()
    result = call(ctx, recorder, 'get_roadmap', {'section': 'goals'}, now=NOW,
                  tools=toolsets.tools_for('propose@agent-4'))
    assert 'error' in result and result['ref'] == 'call:1'
    assert not invoked
    assert recorder.calls[0]['result_sha256']


def test_required_binding_missing_fails_before_model():
    with pytest.raises(ToolError, match='get_roadmap'):
        graph._wrapped_tools(context(), Recorder(), now=lambda: NOW, tools='optimize@agent-optimize-1')


def test_bindings_are_copied_and_cannot_widen_whitelist():
    bindings = {'get_roadmap': lambda ctx, section, query=None: {'cutoff_at': ctx.cutoff_at.isoformat(), 'section': section}}
    ctx = context(bindings=bindings)
    bindings.clear()
    with pytest.raises(TypeError):
        ctx.bindings['other'] = lambda: None
    recorder = Recorder(phase='optimize')
    wrapped = {t.name: t for t in graph._wrapped_tools(ctx, recorder, now=lambda: NOW, tools='optimize@agent-optimize-1')}
    assert set(wrapped) == toolsets.TOOLSETS['optimize@agent-optimize-1']
    result = json.loads(wrapped['get_roadmap'].invoke({'section': 'goals'}))
    assert result == {'ref': 'call:1', 'data': {'cutoff_at': NOW.isoformat(), 'section': 'goals'}}
    assert recorder.calls[0]['phase'] == 'optimize'
