"""The frontend registry's tool snapshots must come from valid, current tool results."""

import json
from pathlib import Path

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.toolsurface.dispatch import call, tool_defs
from investment_assistant.toolsurface.env import make_env

FIXTURES = Path(__file__).parent / "fixtures" / "tool_results"


def test_every_tool_has_a_fixture_and_it_parses():
    names = {tool.name for tool in tool_defs("chat")}
    assert len(names) == 38
    assert {path.stem for path in FIXTURES.glob("*.json")} == names
    for name in sorted(names):
        json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("tool", ["get_market", "get_plans", "get_account", "get_agenda", "get_research"])
def test_fixture_matches_live_shape(v3_tx, tool):
    from scripts.dev.export_tool_fixtures import NOW, SESSION, seed

    seed(v3_tx)
    v3_tx.commit()
    env = make_env(session=SESSION, now=NOW, cfg=AssistantConfig(), mode="chat", agent_session="fixture-shape")
    envelope = call(env, tool, {})
    assert "error" not in envelope, envelope
    snapshot = json.loads((FIXTURES / f"{tool}.json").read_text(encoding="utf-8"))
    live = envelope["data"]
    assert set(snapshot) == set(live)
    # Nonempty row snapshots also retain all original plan/research/account columns.
    for key in set(snapshot) & set(live):
        if isinstance(snapshot[key], dict) and isinstance(live[key], dict):
            assert set(snapshot[key]) == set(live[key])
        elif isinstance(snapshot[key], list) and snapshot[key] and live[key] and isinstance(snapshot[key][0], dict):
            assert set(snapshot[key][0]) == set(live[key][0])
