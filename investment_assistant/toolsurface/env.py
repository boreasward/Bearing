"""The tool environment: one trading session's fixed view every tool reads through."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import investment_assistant.core.tx as core_tx
from investment_assistant.config import AssistantConfig, load_config

MODES = ('brief', 'chat')


@dataclass(frozen=True)
class ToolEnv:
    session: date
    cutoff_at: datetime
    mode: str                 # 'brief' | 'chat'
    agent_session: str
    cfg: AssistantConfig
    read: Callable[[Callable[[Any], Any]], Any]     # 在只读事务里执行 fn(conn)
    write: Callable[[Callable[[Any], Any]], Any]    # 在读写事务里执行 fn(conn)


def make_env(*, session: date, mode: str, agent_session: str, cfg: AssistantConfig | None = None,
             now: datetime | None = None) -> ToolEnv:
    """交易日与数据截止时点一经启动固定不变；模式与会话编号由宿主传入，模型改不了（spec §6）。"""
    if mode not in MODES:
        raise ValueError(f'unknown mode: {mode!r}')
    cfg = cfg if cfg is not None else load_config()
    now = now if now is not None else datetime.now(UTC)

    def _read(fn: Callable[[Any], Any]) -> Any:
        with core_tx.read_only_tx() as conn:
            return fn(conn)

    def _write(fn: Callable[[Any], Any]) -> Any:
        with core_tx._tx() as conn:
            return fn(conn)

    return ToolEnv(session=session, cutoff_at=now, mode=mode, agent_session=agent_session, cfg=cfg,
                   read=_read, write=_write)
