"""文件锁：同进程第二次 acquire 报 Busy(kind)，holder() 读出占用者，runtime_base 走环境覆盖。离线。"""

from pathlib import Path

import pytest
from investment_assistant.runtime import lock

from investment_assistant.config import AssistantConfig, V3Config


def cfg_at(tmp_path: Path) -> AssistantConfig:
    return AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs")))


def test_second_acquire_raises_busy_with_kind(tmp_path):
    cfg = cfg_at(tmp_path)
    with lock.acquire("brief", cfg=cfg):
        with pytest.raises(lock.Busy) as exc, lock.acquire("chat", cfg=cfg):
            pass
        assert exc.value.kind == "chat"
        assert isinstance(exc.value, RuntimeError)


def test_lock_file_records_kind_and_releases(tmp_path):
    cfg = cfg_at(tmp_path)
    with lock.acquire("brief", cfg=cfg):
        assert (lock.runtime_base(cfg) / "pi.lock").read_text(encoding="utf-8") == "brief"
    with lock.acquire("brief", cfg=cfg):  # 释放后可重新获得
        assert lock.holder(cfg) == "brief"


def test_holder_none_when_free_or_absent(tmp_path):
    cfg = cfg_at(tmp_path)
    assert lock.holder(cfg) is None  # 锁文件还不存在
    with lock.acquire("chat", cfg=cfg):
        pass
    assert lock.holder(cfg) is None  # 已释放


def test_runtime_base_follows_env_override(tmp_path, monkeypatch):
    monkeypatch.delenv("INVESTMENT_ASSISTANT_RUNTIME_BASE", raising=False)
    cfg = AssistantConfig()  # briefs_dir 未配置
    monkeypatch.setenv("INVESTMENT_ASSISTANT_RUNTIME_BASE", str(tmp_path / "envbase"))
    assert lock.runtime_base(cfg) == tmp_path / "envbase"
    from investment_assistant.config import briefs_dir

    assert briefs_dir(cfg) == tmp_path / "envbase" / "briefs"
