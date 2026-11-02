"""Shared portfolio-check result type, independent of sizing and order validation."""
from __future__ import annotations

from dataclasses import dataclass

from investment_assistant.core.versions import PORTFOLIO_CHECK_VERSION


@dataclass
class PortfolioResult:
    exposure_by_sector: dict
    verdicts: list[dict]
    conflicts: list[dict]
    order: list
    params_used: dict
    version: str = PORTFOLIO_CHECK_VERSION
