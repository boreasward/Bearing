from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .runtime_paths import DEFAULT_CONFIG_PATH, DEFAULT_FILINGS_DIR


@dataclass(frozen=True)
class FilingsConfig:
    forms: list[str] = field(default_factory=lambda: ["10-Q", "10-K"])
    output_dir: Path = DEFAULT_FILINGS_DIR


@dataclass(frozen=True)
class SecMonitorConfig:
    """Paths of the OpenClaw SEC monitor migration (``data/sec/monitor``)."""

    watchlist_path: str = ""
    legacy_log_path: str = ""
    state_dir: str = ""


@dataclass(frozen=True)
class NotifyConfig:
    """Discord webhook overrides read by ``DiscordClient.from_config``; unset channels fall back to the environment."""

    webhooks: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkbenchConfig:
    """Position-sizing parameters of a trade plan (``execution/plan_levels``)."""

    risk_pct: float = 0.01
    single_cap: float = 0.25
    total_cap: float = 1.0


@dataclass(frozen=True)
class V3Config:
    """v3 agent-first phase parameters (v3 spec §15.6); the v3.1 keys follow the v3.1 design §14
    and the v3.2 keys the v3.2 design §12. Fixed values are the spec's, verbatim."""

    top_n: int = 5
    collect_workers: int = 6
    sec_workers: int = 3
    replay_sessions: int = 20
    hold_sessions: int = 5
    min_triggers: int = 10
    win_hi: float = 0.60
    win_lo: float = 0.40
    min_group: int = 3
    pending_cap: int = 3
    valid_sessions: int = 5
    max_hold_default: int = 20
    trail_atr: float = 3.0
    brief_budget_minutes: int = 20
    chat_turn_seconds: int = 300
    research_sessions: int = 20
    research_entry_atr: float = 4.0
    retro_min_sample: int = 10
    sector_etfs: tuple[str, ...] = (
        "QQQ",
        "SMH",
        "XLK",
        "XLV",
        "XLY",
        "XLP",
        "XLE",
        "XLF",
        "XLI",
        "XLB",
        "XLU",
        "XLRE",
        "XLC",
    )
    briefs_dir: str = ""


@dataclass(frozen=True)
class AssistantConfig:
    filings: FilingsConfig = field(default_factory=FilingsConfig)
    sec_monitor: SecMonitorConfig = field(default_factory=SecMonitorConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    workbench: WorkbenchConfig = field(default_factory=WorkbenchConfig)
    v3: V3Config = field(default_factory=V3Config)


def load_config(path: str | Path | None = None) -> AssistantConfig:
    """Load investment assistant config with runtime-safe defaults."""
    config_path = _resolve_config_path(path)
    data: dict[str, Any] = {}
    if config_path and config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
    return _config_from_dict(data)


def _resolve_config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("INVESTMENT_ASSISTANT_CONFIG")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def _config_from_dict(data: dict[str, Any]) -> AssistantConfig:
    """Known sections override the defaults; any other key (including the sections of the removed v2 system) is ignored."""
    cfg = AssistantConfig()
    if "filings" in data:
        cfg = replace(cfg, filings=_filings_from_dict(data["filings"], cfg.filings))
    if "sec_monitor" in data and isinstance(data["sec_monitor"], dict):
        cfg = replace(cfg, sec_monitor=_dataclass_from_dict(data["sec_monitor"], cfg.sec_monitor))
    if "notify" in data and isinstance(data["notify"], dict):
        cfg = replace(cfg, notify=_dataclass_from_dict(data["notify"], cfg.notify))
    if "workbench" in data:
        cfg = replace(cfg, workbench=_validate_workbench(_dataclass_from_dict(data["workbench"], cfg.workbench)))
    if "v3" in data and isinstance(data["v3"], dict):
        cfg = replace(cfg, v3=_validate_v3(_dataclass_from_dict(data["v3"], cfg.v3)))
    return cfg


def _dataclass_from_dict(data: dict[str, Any], base: Any) -> Any:
    """Shallow override of a frozen dataclass from a dict, casting to field types."""
    from dataclasses import fields

    values: dict[str, Any] = {}
    type_map = {f.name: f.type for f in fields(base)}
    for key, value in data.items():
        if key not in type_map:
            continue
        current = getattr(base, key)
        if isinstance(current, bool):
            values[key] = bool(value)
        elif isinstance(current, int) and not isinstance(current, bool):
            values[key] = int(value)
        elif isinstance(current, float):
            values[key] = float(value)
        elif isinstance(current, list):
            values[key] = list(value)
        else:
            values[key] = value
    return replace(base, **values)


def _filings_from_dict(data: dict[str, Any], base: FilingsConfig) -> FilingsConfig:
    values: dict[str, Any] = {}
    if "forms" in data:
        values["forms"] = _normalize_forms(data["forms"])
    if "output_dir" in data:
        values["output_dir"] = Path(data["output_dir"])
    return replace(base, **values)


def _validate_workbench(cfg: WorkbenchConfig) -> WorkbenchConfig:
    """Range-check the sizing parameters."""
    if not 0 < cfg.risk_pct <= 0.05:
        raise ValueError("workbench.risk_pct must be in (0, 0.05]")
    if not 0 < cfg.single_cap <= 1:
        raise ValueError("workbench.single_cap must be in (0, 1]")
    if not 0 < cfg.total_cap <= 1:
        raise ValueError("workbench.total_cap must be in (0, 1]")
    return cfg


def _validate_v3(cfg: V3Config) -> V3Config:
    if not 1 <= cfg.top_n <= 20:
        raise ValueError("v3.top_n must be in [1, 20]")
    if not 1 <= cfg.collect_workers <= 6:
        raise ValueError("v3.collect_workers must be in [1, 6]")
    if not 1 <= cfg.sec_workers <= 4:
        raise ValueError("v3.sec_workers must be in [1, 4]")
    if not 5 <= cfg.replay_sessions <= 60:
        raise ValueError("v3.replay_sessions must be in [5, 60]")
    if not 1 <= cfg.hold_sessions <= 20:
        raise ValueError("v3.hold_sessions must be in [1, 20]")
    if not 0 < cfg.win_lo < cfg.win_hi < 1:
        raise ValueError("v3.win_lo / v3.win_hi must satisfy 0 < win_lo < win_hi < 1")
    if cfg.min_group < 2:
        raise ValueError("v3.min_group must be >= 2")
    if not 1 <= cfg.pending_cap <= 10:
        raise ValueError("v3.pending_cap must be in [1, 10]")
    if not 1 <= cfg.valid_sessions <= 20:
        raise ValueError("v3.valid_sessions must be in [1, 20]")
    if not 3 <= cfg.max_hold_default <= 30:
        raise ValueError("v3.max_hold_default must be in [3, 30]")
    if not 0 < cfg.trail_atr <= 10:
        raise ValueError("v3.trail_atr must be in (0, 10]")
    if not 1 <= cfg.brief_budget_minutes <= 120:
        raise ValueError("v3.brief_budget_minutes must be in [1, 120]")
    if not 30 <= cfg.chat_turn_seconds <= 1800:
        raise ValueError("v3.chat_turn_seconds must be in [30, 1800]")
    if not 1 <= cfg.research_sessions <= 60:
        raise ValueError("v3.research_sessions must be in [1, 60]")
    if not 2 <= cfg.research_entry_atr <= 8:
        raise ValueError("v3.research_entry_atr must be in [2, 8]")
    if not 1 <= cfg.retro_min_sample <= 100:
        raise ValueError("v3.retro_min_sample must be in [1, 100]")
    return replace(cfg, sector_etfs=tuple(str(etf).strip().upper() for etf in cfg.sector_etfs))


def briefs_dir(cfg: AssistantConfig) -> Path:
    """Where the v3 daily brief lands: cfg.v3.briefs_dir, else $INVESTMENT_ASSISTANT_RUNTIME_BASE/briefs,
    else the repo-local .local/v3/briefs."""
    if cfg.v3.briefs_dir:
        return Path(cfg.v3.briefs_dir)
    base = os.environ.get("INVESTMENT_ASSISTANT_RUNTIME_BASE")
    if base:
        return Path(base) / "briefs"
    return Path(".local/v3/briefs")


def _normalize_forms(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("filings.forms must be a list")
    forms = [str(item).strip().upper() for item in raw if str(item).strip()]
    if not forms:
        raise ValueError("filings.forms must not be empty")
    return forms
