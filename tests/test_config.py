import json
from pathlib import Path

import pytest

from investment_assistant.config import AssistantConfig, load_config


def test_load_config_uses_runtime_defaults_when_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.delenv("INVESTMENT_ASSISTANT_CONFIG", raising=False)

    cfg = load_config(missing)

    assert cfg.filings.forms == ["10-Q", "10-K"]
    assert cfg.filings.output_dir == Path("/srv/investment-assistant/filings")


def test_load_config_applies_json_overrides(tmp_path):
    path = tmp_path / "investment-assistant.json"
    path.write_text(json.dumps({
        "filings": {"forms": ["10-Q"], "output_dir": str(tmp_path / "filings")},
    }))

    cfg = load_config(path)

    assert cfg.filings.forms == ["10-Q"]
    assert cfg.filings.output_dir == tmp_path / "filings"


def test_notify_webhooks_override_from_json(tmp_path):
    path = tmp_path / "investment-assistant.json"
    path.write_text(json.dumps({"notify": {"webhooks": {"signals": "https://hook/signals"}}}))

    assert load_config(path).notify.webhooks == {"signals": "https://hook/signals"}


def test_workbench_defaults(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.delenv("INVESTMENT_ASSISTANT_CONFIG", raising=False)

    cfg = load_config(missing)

    assert cfg.workbench.risk_pct == 0.01
    assert cfg.workbench.single_cap == 0.25
    assert cfg.workbench.total_cap == 1.0


def test_workbench_section_overrides(tmp_path):
    path = tmp_path / "investment-assistant.json"
    path.write_text(json.dumps({"workbench": {"risk_pct": 0.015}}))

    cfg = load_config(path)

    assert cfg.workbench.risk_pct == 0.015
    assert cfg.workbench.single_cap == 0.25


def test_workbench_rejects_bad_values(tmp_path):
    def load_with(workbench):
        path = tmp_path / "investment-assistant.json"
        path.write_text(json.dumps({"workbench": workbench}))
        return load_config(path)

    for bad in ({"risk_pct": 0}, {"risk_pct": 0.06}, {"single_cap": 0}, {"single_cap": 1.5},
                {"total_cap": 0}, {"total_cap": -1}):
        with pytest.raises(ValueError):
            load_with(bad)


def test_load_config_ignores_removed_v2_keys(tmp_path):
    """本机 config/local.json 里还留着 v2 的键时照常加载。"""
    path = tmp_path / 'c.json'
    path.write_text(json.dumps({
        'watchlist': ['AAPL'], 'vault_ro': '/srv/vault-ro', 'brief_time_local': '08:30', 'max_daily_focus_items': 2,
        'macro': {'lookback_days': 10}, 'fundamentals': {'units': 'USD'}, 'prices': {'history_days': 7},
        'workbench': {'x': 1, 'public_url': 'http://localhost:8787', 'return_tiers': [0.2, 0.1, 0.0]},
        'notify': {'channel': 'email', 'task_enabled': {'filings': False}, 'webhooks': {'signals': 'https://hook'}},
        'sec_monitor': {'enabled': True, 'start_date': '2026-10-01', 'discord_channel_id': '1'},
        'filings': {'lookback_years': 3}, 'unknown': 3, 'v3': {},
    }), encoding='utf-8')
    cfg = load_config(path)
    assert cfg.v3 == AssistantConfig().v3
    assert cfg.workbench == AssistantConfig().workbench
    assert cfg.filings == AssistantConfig().filings
    assert cfg.notify.webhooks == {'signals': 'https://hook'}
    assert cfg.sec_monitor == AssistantConfig().sec_monitor
