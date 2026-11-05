from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .runtime_paths import DEFAULT_CONFIG_PATH, DEFAULT_DRAFT_DIR, DEFAULT_FILINGS_DIR, DEFAULT_VAULT_RO


@dataclass(frozen=True)
class FilingsConfig:
    forms: list[str] = field(default_factory=lambda: ["10-Q", "10-K"])
    lookback_years: int = 3
    output_dir: Path = DEFAULT_FILINGS_DIR


@dataclass(frozen=True)
class SecMonitorConfig:
    """Opt-in migration of the OpenClaw SEC monitor; secrets stay in the environment."""

    enabled: bool = False
    start_date: str = ""
    watchlist_path: str = ""
    legacy_log_path: str = ""
    state_dir: str = ""
    discord_channel_id: str = ""


@dataclass(frozen=True)
class NotifyConfig:
    """Notification channel toggles for the scheduled tasks."""

    channel: str = "discord"  # "discord" | "email"（全局单选）
    discord_enabled: bool = True
    webhooks: dict[str, str] = field(default_factory=dict)
    email: dict[str, Any] = field(default_factory=lambda: {
        "from_addr": "", "to_addr": "", "host": "smtp.gmail.com", "port": 465,
    })
    task_channels: dict[str, str] = field(
        default_factory=lambda: {"filings": "earnings", "workbench": "signals",
                                 "workbench_review": "signals"}
    )
    task_enabled: dict[str, bool] = field(
        default_factory=lambda: {"filings": True, "workbench": True, "workbench_review": True}
    )


@dataclass(frozen=True)
class MacroConfig:
    """FRED macro-indicator collection (Phase 2)."""

    fred_series: list[str] = field(
        default_factory=lambda: ["DGS10", "DGS2", "FEDFUNDS", "CPIAUCSL", "UNRATE", "BAMLH0A0HYM2"]
    )
    lookback_days: int = 400


@dataclass(frozen=True)
class WorkbenchConfig:
    """P1 trade plan account parameters (design §4.3, §8.2)."""

    risk_pct: float = 0.01
    single_cap: float = 0.25
    total_cap: float = 1.0
    public_url: str = "http://localhost:8787"
    return_tiers: tuple[float, float, float] = (0.20, 0.10, 0.0)


@dataclass(frozen=True)
class FundamentalsConfig:
    """SEC XBRL companyconcept ingestion (Phase 2)."""

    concepts: list[str] = field(
        default_factory=lambda: ["Revenues", "NetIncomeLoss", "EarningsPerShareDiluted"]
    )
    units: str = "USD"


@dataclass(frozen=True)
class PriceConfig:
    """OHLCV collection. Timeouts and retries live in ``data/fetch_policy`` (fetch-1), not here."""

    primary: str = "yfinance"
    history_days: int = 400


@dataclass(frozen=True)
class V3Config:
    """v3 agent-first phase parameters (v3 spec §15.6); fixed values are the spec's, verbatim."""

    top_n: int = 5
    collect_workers: int = 6
    sec_workers: int = 3
    replay_sessions: int = 20
    hold_sessions: int = 5
    min_triggers: int = 10
    win_hi: float = 0.60
    win_lo: float = 0.40
    min_group: int = 3
    sector_etfs: tuple[str, ...] = ('QQQ', 'SMH', 'XLK', 'XLV', 'XLY', 'XLP', 'XLE', 'XLF',
                                    'XLI', 'XLB', 'XLU', 'XLRE', 'XLC')
    briefs_dir: str = ''


@dataclass(frozen=True)
class AssistantConfig:
    watchlist: list[str] = field(default_factory=lambda: ["CRDO", "MU", "RKLB", "NVDA"])
    filings: FilingsConfig = field(default_factory=FilingsConfig)
    sec_monitor: SecMonitorConfig = field(default_factory=SecMonitorConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    macro: MacroConfig = field(default_factory=MacroConfig)
    workbench: WorkbenchConfig = field(default_factory=WorkbenchConfig)
    fundamentals: FundamentalsConfig = field(default_factory=FundamentalsConfig)
    prices: PriceConfig = field(default_factory=PriceConfig)
    v3: V3Config = field(default_factory=V3Config)
    vault_ro: Path = DEFAULT_VAULT_RO
    draft_dir: Path = DEFAULT_DRAFT_DIR
    brief_time_local: str = "08:30"
    max_daily_focus_items: int = 3


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
    cfg = AssistantConfig()
    if "watchlist" in data:
        cfg = replace(cfg, watchlist=_normalize_tickers(data["watchlist"]))
    if "filings" in data:
        cfg = replace(cfg, filings=_filings_from_dict(data["filings"], cfg.filings))
    if "sec_monitor" in data and isinstance(data["sec_monitor"], dict):
        cfg = replace(cfg, sec_monitor=_dataclass_from_dict(data["sec_monitor"], cfg.sec_monitor))
    if "vault_ro" in data:
        cfg = replace(cfg, vault_ro=Path(data["vault_ro"]))
    if "draft_dir" in data:
        cfg = replace(cfg, draft_dir=Path(data["draft_dir"]))
    if "brief_time_local" in data:
        cfg = replace(cfg, brief_time_local=str(data["brief_time_local"]))
    if "max_daily_focus_items" in data:
        cfg = replace(cfg, max_daily_focus_items=int(data["max_daily_focus_items"]))
    if "notify" in data and isinstance(data["notify"], dict):
        cfg = replace(cfg, notify=_dataclass_from_dict(data["notify"], cfg.notify))
    if "macro" in data and isinstance(data["macro"], dict):
        cfg = replace(cfg, macro=_dataclass_from_dict(data["macro"], cfg.macro))
    if "workbench" in data:
        cfg = replace(cfg, workbench=_validate_workbench(_dataclass_from_dict(data["workbench"], cfg.workbench)))
    if "v3" in data and isinstance(data["v3"], dict):
        cfg = replace(cfg, v3=_validate_v3(_dataclass_from_dict(data["v3"], cfg.v3)))
    if "prices" in data and isinstance(data["prices"], dict):
        cfg = replace(cfg, prices=_dataclass_from_dict(data["prices"], cfg.prices))
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
    if "lookback_years" in data:
        values["lookback_years"] = int(data["lookback_years"])
    if "output_dir" in data:
        values["output_dir"] = Path(data["output_dir"])
    return replace(base, **values)


def _validate_workbench(cfg: WorkbenchConfig) -> WorkbenchConfig:
    """JSON lists stay lists through _dataclass_from_dict; normalize and range-check here."""
    tiers = tuple(float(t) for t in cfg.return_tiers)
    if not 0 < cfg.risk_pct <= 0.05:
        raise ValueError("workbench.risk_pct must be in (0, 0.05]")
    if not 0 < cfg.single_cap <= 1:
        raise ValueError("workbench.single_cap must be in (0, 1]")
    if not 0 < cfg.total_cap <= 1:
        raise ValueError("workbench.total_cap must be in (0, 1]")
    if len(tiers) != 3 or not all(a > b for a, b in zip(tiers, tiers[1:])):
        raise ValueError("workbench.return_tiers must be three strictly descending thresholds")
    return replace(cfg, return_tiers=tiers)


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


def _normalize_tickers(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("watchlist must be a list of ticker symbols")
    tickers = [str(item).strip().upper() for item in raw if str(item).strip()]
    if not tickers:
        raise ValueError("watchlist must not be empty")
    return tickers


def _normalize_forms(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("filings.forms must be a list")
    forms = [str(item).strip().upper() for item in raw if str(item).strip()]
    if not forms:
        raise ValueError("filings.forms must not be empty")
    return forms
