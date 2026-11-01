"""The in-process background runner for API-submitted jobs (was ``tasks/runner.py``).

Cut verbatim in Task 0; ``tasks/runner.py`` re-exports until Task 1."""
from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from queue import Full, Queue
from typing import Any, Callable

_LOCK = threading.Lock()
_RUNS: dict[str, dict[str, Any]] = {}
_SUBSCRIBERS: list[Queue] = []


def _now() -> str:
    return datetime.now(UTC).isoformat()


def submit(kind: str, fn: Callable[..., dict[str, Any]], *, with_run_id: bool = False) -> str:
    """Queue fn in the background and return its run_id.

    with_run_id=True calls fn(run_id) with the returned run_id (v2.3 §7: the manual
    fetch records its data_fetch_runs row under the same id); otherwise fn().
    """
    run_id = f"{kind}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    with _LOCK:
        _RUNS[run_id] = {"run_id": run_id, "kind": kind, "status": "pending", "created_at": _now()}

    def _worker() -> None:
        try:
            result = fn(run_id) if with_run_id else fn()
            update: dict[str, Any] = {"status": "done", "result": result, "finished_at": _now()}
        except Exception as exc:  # structured record, not swallowed
            update = {"status": "error", "error": str(exc), "finished_at": _now()}
        with _LOCK:
            _RUNS[run_id].update(update)
        _publish({"run_id": run_id, "status": update["status"], "kind": kind})

    threading.Thread(target=_worker, name=f"run-{run_id}", daemon=True).start()
    return run_id


def get(run_id: str) -> dict[str, Any] | None:
    with _LOCK:
        rec = _RUNS.get(run_id)
        return dict(rec) if rec else None


def subscribe() -> Queue:
    q: Queue = Queue(maxsize=100)
    with _LOCK:
        _SUBSCRIBERS.append(q)
    return q


def unsubscribe(q: Queue) -> None:
    with _LOCK:
        if q in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(q)


def _publish(event: dict[str, Any]) -> None:
    with _LOCK:
        subs = list(_SUBSCRIBERS)
    for q in subs:
        try:
            q.put_nowait(event)
        except Full:
            pass
