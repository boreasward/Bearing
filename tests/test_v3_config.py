"""V3Config defaults, file overrides, range validation and the briefs_dir resolution order."""

import json
from pathlib import Path

import pytest

from investment_assistant.config import briefs_dir, load_config


def _write(tmp_path, payload):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.v3.top_n == 5 and cfg.v3.hold_sessions == 5 and cfg.v3.min_group == 3
    assert cfg.v3.sector_etfs[:3] == ("QQQ", "SMH", "XLK") and len(cfg.v3.sector_etfs) == 13


def test_v3_1_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert (cfg.v3.pending_cap, cfg.v3.valid_sessions, cfg.v3.max_hold_default) == (3, 5, 20)
    assert (cfg.v3.trail_atr, cfg.v3.brief_budget_minutes, cfg.v3.chat_turn_seconds) == (3.0, 20, 300)


def test_v3_1_ranges(tmp_path):
    for bad in (
        {"pending_cap": 0},
        {"pending_cap": 11},
        {"valid_sessions": 0},
        {"valid_sessions": 21},
        {"max_hold_default": 2},
        {"max_hold_default": 31},
        {"trail_atr": 0},
        {"trail_atr": 10.5},
        {"brief_budget_minutes": 0},
        {"brief_budget_minutes": 121},
        {"chat_turn_seconds": 29},
        {"chat_turn_seconds": 1801},
    ):
        with pytest.raises(ValueError):
            load_config(_write(tmp_path, {"v3": bad}))
    ok = {
        "pending_cap": 10,
        "valid_sessions": 20,
        "max_hold_default": 30,
        "trail_atr": 10,
        "brief_budget_minutes": 120,
        "chat_turn_seconds": 1800,
    }
    cfg = load_config(_write(tmp_path, {"v3": ok}))  # 边界值本身合法
    assert (cfg.v3.pending_cap, cfg.v3.trail_atr, cfg.v3.chat_turn_seconds) == (10, 10, 1800)


def test_file_overrides_and_validation(tmp_path):
    cfg = load_config(_write(tmp_path, {"v3": {"top_n": 3, "win_hi": 0.7}}))
    assert cfg.v3.top_n == 3 and cfg.v3.win_hi == 0.7
    for bad in (
        {"top_n": 0},
        {"top_n": 21},
        {"collect_workers": 7},
        {"sec_workers": 5},
        {"replay_sessions": 4},
        {"hold_sessions": 0},
        {"win_lo": 0.6, "win_hi": 0.6},
        {"win_lo": 0.8, "win_hi": 0.6},
        {"min_group": 1},
    ):
        with pytest.raises(ValueError):
            load_config(_write(tmp_path, {"v3": bad}))


def test_briefs_dir(monkeypatch, tmp_path):
    cfg = load_config(_write(tmp_path, {"v3": {"briefs_dir": str(tmp_path / "briefs")}}))
    assert briefs_dir(cfg) == tmp_path / "briefs"

    monkeypatch.delenv("INVESTMENT_ASSISTANT_RUNTIME_BASE", raising=False)
    cfg = load_config(tmp_path / "missing.json")
    assert briefs_dir(cfg) == Path(".local/v3/briefs")

    monkeypatch.setenv("INVESTMENT_ASSISTANT_RUNTIME_BASE", str(tmp_path / "runtime"))
    assert briefs_dir(cfg) == tmp_path / "runtime" / "briefs"
