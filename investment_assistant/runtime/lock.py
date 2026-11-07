"""One unattended runtime process at a time (spec §9.1): a non-blocking flock on
``runtime_base/pi.lock`` shared by the brief session and every chat turn."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from investment_assistant.config import AssistantConfig, briefs_dir, load_config

LOCK_NAME = "pi.lock"


def runtime_base(cfg: AssistantConfig | None = None) -> Path:
    """The base of all runtime files: ``INVESTMENT_ASSISTANT_RUNTIME_BASE`` (default ``.local/v3``),
    i.e. the parent of ``briefs_dir``."""
    return briefs_dir(cfg if cfg is not None else load_config()).parent


class Busy(RuntimeError):
    """Another session holds the lock; ``kind`` says which ('brief' | 'chat')."""

    def __init__(self, kind: str):
        super().__init__(f"已有 {kind} 会话在运行")
        self.kind = kind


def _lock_path(cfg: AssistantConfig | None) -> Path:
    return runtime_base(cfg) / LOCK_NAME


@contextmanager
def acquire(kind: str, *, cfg: AssistantConfig | None = None):
    """Hold the lock for the block; a second acquirer gets ``Busy`` immediately."""
    path = _lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(kind) from None
        os.truncate(fd, 0)
        os.write(fd, kind.encode("utf-8"))
        yield
    finally:
        os.close(fd)  # 关闭即解锁（flock 随 open file description 释放）


def holder(cfg: AssistantConfig | None = None) -> str | None:
    """Which kind of session currently holds the lock, or None when free/absent."""
    path = _lock_path(cfg)
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return path.read_text(encoding="utf-8").strip() or "unknown"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)
