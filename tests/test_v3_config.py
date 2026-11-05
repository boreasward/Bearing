"""V3Config defaults, file overrides, range validation and the briefs_dir resolution order."""
import json
from pathlib import Path

import pytest

from investment_assistant.config import briefs_dir, load_config


def _write(tmp_path, payload):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def test_defaults(tmp_path):
    cfg = load_config(tmp_path / 'missing.json')
    assert cfg.v3.top_n == 5 and cfg.v3.hold_sessions == 5 and cfg.v3.min_group == 3
    assert cfg.v3.sector_etfs[:3] == ('QQQ', 'SMH', 'XLK') and len(cfg.v3.sector_etfs) == 13


def test_file_overrides_and_validation(tmp_path):
    cfg = load_config(_write(tmp_path, {'v3': {'top_n': 3, 'win_hi': 0.7}}))
    assert cfg.v3.top_n == 3 and cfg.v3.win_hi == 0.7
    for bad in ({'top_n': 0}, {'top_n': 21}, {'collect_workers': 7}, {'sec_workers': 5},
                {'replay_sessions': 4}, {'hold_sessions': 0}, {'win_lo': 0.6, 'win_hi': 0.6},
                {'win_lo': 0.8, 'win_hi': 0.6}, {'min_group': 1}):
        with pytest.raises(ValueError):
            load_config(_write(tmp_path, {'v3': bad}))


def test_briefs_dir(monkeypatch, tmp_path):
    cfg = load_config(_write(tmp_path, {'v3': {'briefs_dir': str(tmp_path / 'briefs')}}))
    assert briefs_dir(cfg) == tmp_path / 'briefs'

    monkeypatch.delenv('INVESTMENT_ASSISTANT_RUNTIME_BASE', raising=False)
    cfg = load_config(tmp_path / 'missing.json')
    assert briefs_dir(cfg) == Path('.local/v3/briefs')

    monkeypatch.setenv('INVESTMENT_ASSISTANT_RUNTIME_BASE', str(tmp_path / 'runtime'))
    assert briefs_dir(cfg) == tmp_path / 'runtime' / 'briefs'
