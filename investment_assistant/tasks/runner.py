"""Re-export of the background runner, which moved to ``investment_assistant/core/runner.py``
in Task 0 (v2.12). This module keeps the historical import path until Task 1 deletes it."""
from investment_assistant.core.runner import (  # noqa: F401
    _LOCK as _LOCK,
    _RUNS as _RUNS,
    _SUBSCRIBERS as _SUBSCRIBERS,
    _now as _now,
    _publish as _publish,
    get as get,
    submit as submit,
    subscribe as subscribe,
    unsubscribe as unsubscribe,
)
