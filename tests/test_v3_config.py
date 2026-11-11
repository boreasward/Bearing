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


def test_v3_2_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert (cfg.v3.research_sessions, cfg.v3.research_entry_atr, cfg.v3.retro_min_sample) == (20, 4.0, 10)


def test_v3_2_ranges(tmp_path):
    for bad, message in (
        ({"research_sessions": 0}, r"v3\.research_sessions must be in \[1, 60\]"),
        ({"research_sessions": 61}, r"v3\.research_sessions must be in \[1, 60\]"),
        ({"research_entry_atr": 1.9}, r"v3\.research_entry_atr must be in \[2, 8\]"),
        ({"research_entry_atr": 8.1}, r"v3\.research_entry_atr must be in \[2, 8\]"),
        ({"retro_min_sample": 0}, r"v3\.retro_min_sample must be in \[1, 100\]"),
        ({"retro_min_sample": 101}, r"v3\.retro_min_sample must be in \[1, 100\]"),
    ):
        with pytest.raises(ValueError, match=message):
            load_config(_write(tmp_path, {"v3": bad}))
    ok = {"research_sessions": 60, "research_entry_atr": 8, "retro_min_sample": 100}
    cfg = load_config(_write(tmp_path, {"v3": ok}))  # 边界值本身合法
    assert (cfg.v3.research_sessions, cfg.v3.research_entry_atr, cfg.v3.retro_min_sample) == (60, 8.0, 100)


def test_v3_3_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.workbench.total_cap == 0.6
    assert cfg.v3.rotation_cooldown_sessions == 5
    assert cfg.v3.rotation_min_fill == 0.5
    assert cfg.v3.rotation_fee_trades == 20
    assert cfg.v3.rotation_horizons == (5, 10, 20)


def test_v3_3_ranges(tmp_path):
    horizons = r"v3\.rotation_horizons must be 1-5 ascending integers in \[1, 60\]"
    for bad, message in (
        ({"rotation_cooldown_sessions": 0}, r"v3\.rotation_cooldown_sessions must be in \[1, 20\]"),
        ({"rotation_cooldown_sessions": 21}, r"v3\.rotation_cooldown_sessions must be in \[1, 20\]"),
        ({"rotation_min_fill": 0}, r"v3\.rotation_min_fill must be in \(0, 1\]"),
        ({"rotation_min_fill": 1.1}, r"v3\.rotation_min_fill must be in \(0, 1\]"),
        ({"rotation_fee_trades": 0}, r"v3\.rotation_fee_trades must be in \[1, 200\]"),
        ({"rotation_fee_trades": 201}, r"v3\.rotation_fee_trades must be in \[1, 200\]"),
        ({"rotation_horizons": []}, horizons),
        ({"rotation_horizons": [1, 2, 3, 4, 5, 6]}, horizons),
        ({"rotation_horizons": [0, 5]}, horizons),
        ({"rotation_horizons": [5, 61]}, horizons),
        ({"rotation_horizons": [10, 5]}, horizons),
        ({"rotation_horizons": [5, 5]}, horizons),
        ({"rotation_horizons": [5, 10.5]}, horizons),
    ):
        with pytest.raises(ValueError, match=message):
            load_config(_write(tmp_path, {"v3": bad}))
    ok = {
        "rotation_cooldown_sessions": 20,
        "rotation_min_fill": 1,
        "rotation_fee_trades": 200,
        "rotation_horizons": [1, 2, 3, 4, 60],
    }
    cfg = load_config(_write(tmp_path, {"v3": ok}))  # 边界值本身合法
    assert (cfg.v3.rotation_cooldown_sessions, cfg.v3.rotation_min_fill, cfg.v3.rotation_fee_trades) == (20, 1.0, 200)
    assert cfg.v3.rotation_horizons == (1, 2, 3, 4, 60)  # JSON 列表读成元组
    low = {"rotation_cooldown_sessions": 1, "rotation_min_fill": 0.01, "rotation_fee_trades": 1}
    low["rotation_horizons"] = [7]
    assert load_config(_write(tmp_path, {"v3": low})).v3.rotation_horizons == (7,)


def test_example_config_matches_the_v3_3_defaults(tmp_path):
    example = load_config(Path(__file__).resolve().parents[1] / "config" / "investment-assistant.example.json")
    assert example.workbench.total_cap == 0.6
    assert example.v3.rotation_horizons == (5, 10, 20) and example.v3.rotation_fee_trades == 20


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
