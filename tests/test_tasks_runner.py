import time
from investment_assistant.core import runner


def test_submit_runs_in_background_and_completes():
    rid = runner.submit("demo", lambda: {"value": 42})
    for _ in range(50):
        rec = runner.get(rid)
        if rec and rec["status"] == "done":
            break
        time.sleep(0.02)
    assert rec["status"] == "done" and rec["result"]["value"] == 42


def test_failure_recorded_structured():
    def boom():
        raise RuntimeError("kaboom")
    rid = runner.submit("demo", boom)
    for _ in range(50):
        rec = runner.get(rid)
        if rec and rec["status"] == "error":
            break
        time.sleep(0.02)
    assert rec["status"] == "error" and "kaboom" in rec["error"]


def test_get_unknown_returns_none():
    assert runner.get("nope") is None


def test_submit_passes_run_id():
    seen = []
    rid = runner.submit("demo", lambda run_id: seen.append(run_id) or {"value": 1}, with_run_id=True)
    for _ in range(50):
        if seen:
            break
        time.sleep(0.02)
    assert seen == [rid]
