from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from investment_assistant.core import tx as service
from investment_assistant.workflow import recorder as stages_module

@dataclass
class NodeContext:
    """What one execution hands every node (spec §4.3).

    ``window_start`` / ``breadth_tickers`` / ``legacy`` are for the v2.7 ``run_batch`` shell and stay unset in the graph,
    which sets ``record_trigger`` to the run's trigger and ``job`` to ``'daily'``."""
    recorder: stages_module.StageRecorder | None
    deps: service.Deps
    clock: Callable[[], float]
    wall_now: Callable[[], datetime] | None
    notify_cfg: Any
    send: Callable | None = None       # required by sending nodes; supplied at the task/API edge
    sec_scan: Callable | None = None   # SEC collection/delivery port, supplied by tasks.filings
    resumed: bool = False
    sleep: Callable[[float], None] = time.sleep
    record_trigger: str | None = None
    job: str = 'workbench'
    window_start: float | None = None
    breadth_tickers: list[str] | None = None
    execution_started: datetime | None = None   # wall time this execution began (a resume restarts the agent window)
    legacy: bool = False               # the v2.7 ``run_batch`` shell: no agent wall stamp, no screen back-fill rows
    config: Any = None                 # the AssistantConfig (``sec_monitor`` reads it); load_config() when None
