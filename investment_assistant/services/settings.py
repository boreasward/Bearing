from __future__ import annotations

import os
import re
from dataclasses import replace
from typing import Any, Callable

from investment_assistant import db
from investment_assistant.config import NotifyConfig
from investment_assistant.core import features as core_features, schema as core_schema, tx as core_tx
from investment_assistant.db import connect
from investment_assistant.notify.discord import DiscordChannel, DiscordClient
from investment_assistant.notify.email import EmailClient

_ENV_KEYS = [
    "SEC_USER_AGENT",
    "INVESTMENT_ASSISTANT_DATABASE_URL",
    "DISCORD_WEBHOOK_EARNINGS",
    "DISCORD_WEBHOOK_SIGNALS",
    "SMTP_PASSWORD",
    "WORKBENCH_AGENT_PROVIDER",
    "WORKBENCH_CHATGPT_CALLBACK_PORT",
]

_EMAIL_FIELDS = {"from_addr", "to_addr", "host", "port"}
NOTIFYING_TASKS = ("filings", "workbench", "workbench_review")
CHANNELS = ("earnings", "signals")


def _filter_tasks(mapping: dict, default: dict) -> dict:
    """Rows kept in notify_settings for removed tasks are ignored, not shown or written back."""
    return {k: v for k, v in {**default, **(mapping or {})}.items() if k in NOTIFYING_TASKS}


def _filter_channels(mapping: dict, default: dict) -> dict:
    return {k: v for k, v in {**default, **(mapping or {})}.items() if k in CHANNELS}


def _has_db() -> bool:
    return bool(os.environ.get("INVESTMENT_ASSISTANT_DATABASE_URL"))


def _with_conn(fn: Callable[[Any], Any]) -> Any:
    with connect(os.environ["INVESTMENT_ASSISTANT_DATABASE_URL"]) as conn:
        return fn(conn)


def _stored() -> dict[str, Any]:
    if not _has_db():
        return {}
    return _with_conn(db.get_notify_settings) or {}


def effective_notify_config(base: NotifyConfig) -> NotifyConfig:
    stored = _stored()
    if not stored:
        return base
    return replace(
        base,
        channel=stored.get("channel") or base.channel,
        discord_enabled=stored.get("discord_enabled", base.discord_enabled),
        webhooks=_filter_channels(stored.get("webhooks") or {}, base.webhooks),
        email={**base.email, **(stored.get("email") or {})},
        task_channels=_filter_tasks(stored.get("task_channels") or {}, base.task_channels),
        task_enabled=_filter_tasks(stored.get("task_enabled") or {}, base.task_enabled),
    )


def read_notify_view() -> dict[str, Any]:
    stored = _stored()
    base = NotifyConfig()
    webhooks = _filter_channels(stored.get("webhooks") or {}, base.webhooks)
    return {
        "channel": stored.get("channel") or base.channel,
        "discord_enabled": stored.get("discord_enabled", base.discord_enabled),
        "email": {**base.email, **(stored.get("email") or {})},  # 地址非敏感；密码只在环境变量
        "task_channels": _filter_tasks(stored.get("task_channels") or {}, base.task_channels),
        "task_enabled": _filter_tasks(stored.get("task_enabled") or {}, base.task_enabled),
        "webhooks": {ch: {"configured": bool(url)} for ch, url in webhooks.items()},
        "degraded": not _has_db(),
    }


def update_notify(payload: dict[str, Any]) -> dict[str, Any]:
    channel = payload.get("channel")
    if channel is not None and channel not in ("discord", "email"):
        return {"updated": False, "error": "invalid channel"}
    if not _has_db():
        return {"updated": False, "degraded": True}
    webhooks = {k: v for k, v in (payload.get("webhooks") or {}).items()
                if k in CHANNELS and str(v).strip()}  # 留空不覆盖
    email = {k: v for k, v in (payload.get("email") or {}).items()
              if k in _EMAIL_FIELDS and str(v).strip()}
    task_channels = {k: v for k, v in (payload.get("task_channels") or {}).items()
                     if k in NOTIFYING_TASKS}
    task_enabled = {k: v for k, v in (payload.get("task_enabled") or {}).items()
                    if k in NOTIFYING_TASKS}
    _with_conn(lambda conn: db.update_notify_settings(
        conn,
        discord_enabled=payload.get("discord_enabled"),
        webhooks=webhooks or None,
        task_channels=task_channels or None,
        task_enabled=task_enabled or None,
        channel=channel,
        email=email or None,
    ))
    return {"updated": True}


def test_notify_channel(channel: str, url: str | None = None, *,
                        email: dict[str, Any] | None = None, client=None) -> dict[str, Any]:
    if channel == "email":
        return _test_email(email or {}, client=client)
    ch = DiscordChannel(channel)
    if client is None:
        cfg = effective_notify_config(NotifyConfig())
        target = url or cfg.webhooks.get(channel)
        if not target:
            return {"ok": False, "error": "no webhook configured"}
        client = DiscordClient(**{f"{c.value}_url": (target if c == ch else "") for c in DiscordChannel})
    payload = {"content": "✅ 测试消息：webhook 配置正常，可安全忽略。"}
    try:
        client.send(ch, payload)
        return {"ok": True}
    except Exception as exc:  # 测试失败结构化返回，不抛；脱敏 webhook 明文
        msg = str(exc)
        # redact any discord webhook URL (with token) that requests may embed in the error
        msg = re.sub(r"https?://\S*discord(?:app)?\.com/api/webhooks/\S+", "<webhook redacted>", msg)
        return {"ok": False, "error": msg}


def _test_email(override: dict[str, Any], *, client=None) -> dict[str, Any]:
    if client is None:
        cfg = effective_notify_config(NotifyConfig())
        merged = {**cfg.email, **{k: v for k, v in override.items() if str(v).strip()}}
        if not os.environ.get("SMTP_PASSWORD"):
            return {"ok": False, "error": "SMTP_PASSWORD not set"}
        if not merged.get("to_addr"):
            return {"ok": False, "error": "no recipient configured"}
        if not merged.get("from_addr"):
            return {"ok": False, "error": "no sender configured"}
        client = EmailClient.from_config(replace(cfg, email=merged))
    try:
        client.send("✅ 测试邮件", "<p>测试邮件：SMTP 配置正常，可安全忽略。</p>")
        return {"ok": True}
    except Exception as exc:  # 测试失败结构化返回，不抛（SMTP 异常不携带密码）
        return {"ok": False, "error": str(exc)}


def read_env_status() -> dict[str, bool]:
    return {key: bool(os.environ.get(key)) for key in _ENV_KEYS}


# --- feature switches (v2.12–v2.15 spec §9, UI1): append-only changes, a flag without history is on ---

def _features_conn(conn) -> None:
    if not core_schema.five_layers_ready(conn):
        raise core_tx.Unavailable('migration_031_missing')


def read_features() -> dict[str, Any]:
    """``{'flags': [{flag, enabled, changed_at, reason, history_count}], 'history': [FeatureChange…]}``: the current
    values and the real change rows (oldest first) — a default is never shown as a history row."""
    with core_tx._tx() as conn:
        _features_conn(conn)
        history = core_features.history(conn)
        current = core_features.current(conn)
    flags = []
    for flag in core_features.FLAGS:
        own = [row for row in history if row['flag'] == flag]
        last = own[-1] if own else None
        flags.append({'flag': flag, 'enabled': current[flag],
                      'changed_at': last['created_at'] if last else None,
                      'reason': last['reason'] if last else None, 'history_count': len(own)})
    return core_tx._jsonable({'flags': flags, 'history': history})


def set_feature(flag: str, *, enabled: Any, reason: Any, request_id: Any) -> dict[str, Any]:
    """Record one switch change (reason required for on and off alike) and return ``{flag, enabled, changed_at}``;
    the same ``request_id`` and content replays the first answer, other content conflicts (``Conflict``)."""
    with core_tx._tx() as conn:
        _features_conn(conn)
        row = core_features.record_change(conn, flag=flag, enabled=enabled, reason=reason, request_id=request_id)
    return core_tx._jsonable({'flag': row['flag'], 'enabled': row['enabled_after'], 'changed_at': row['created_at']})
