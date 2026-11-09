"""Tool result persistence: complete envelopes, bounded audit rows and unchanged full-payload hashes."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from investment_assistant.config import AssistantConfig
from investment_assistant.facts import repo as facts_repo
from investment_assistant.toolsurface import dispatch
from investment_assistant.toolsurface.env import make_env
from investment_assistant.toolsurface.validation import ToolError

SESSION = date(2026, 11, 4)
NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)


def make_tool_env(*, mode="chat", max_bytes=524288):
    cfg = AssistantConfig()
    return make_env(
        session=SESSION,
        mode=mode,
        agent_session="t-dispatch",
        cfg=replace(cfg, v3=replace(cfg.v3, tool_result_max_bytes=max_bytes)),
        now=NOW,
    )


def patch_result(monkeypatch, payload):
    monkeypatch.setitem(
        dispatch._BY_NAME,
        "get_account",
        replace(dispatch._BY_NAME["get_account"], fn=lambda env: payload),
    )


def serialized(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("mode", ["brief", "chat"])
def test_result_is_stored_with_the_call(v3_tx, mode):
    env = make_tool_env(mode=mode)
    envelope = dispatch.call(env, "get_account", {})
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert envelope["ref"] == "call:1"
    assert row["result"] == envelope["data"]
    assert row["truncated"] is False
    assert row["result_sha256"] == hashlib.sha256(serialized(envelope["data"])).hexdigest()


def test_error_envelope_is_stored_as_error_dict(v3_tx):
    env = make_tool_env()
    envelope = dispatch.call(env, "get_price_bars", {"ticker": "AAPL", "lookback": "bad"})
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert envelope["error"].startswith("invalid_argument: ")
    assert row["result"] == {"error": envelope["error"]}
    assert row["error"] == envelope["error"]
    assert row["truncated"] is False
    assert row["result_sha256"] is None


def test_error_detail_is_stored(v3_tx, monkeypatch):
    def reject(env):
        raise ToolError("active_plan_exists", detail={"plan_id": 17, "session": SESSION})

    monkeypatch.setitem(dispatch._BY_NAME, "get_account", replace(dispatch._BY_NAME["get_account"], fn=reject))
    env = make_tool_env()
    envelope = dispatch.call(env, "get_account", {})
    assert envelope == {
        "ref": "call:1", "error": "active_plan_exists", "detail": {"plan_id": 17, "session": "2026-11-04"}
    }
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert row["result"] == {"error": envelope["error"], "detail": envelope["detail"]}


def test_oversized_result_is_truncated_in_db_but_returned_whole(v3_tx, monkeypatch):
    payload = ["数" * 24000]  # UTF-8 size exceeds 70000 bytes, despite fewer characters.
    patch_result(monkeypatch, payload)
    env = make_tool_env(max_bytes=65536)
    envelope = dispatch.call(env, "get_account", {})
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert envelope == {"ref": "call:1", "data": payload}
    assert row["result"] == {"truncated": True, "bytes": len(serialized(payload))}
    assert row["truncated"] is True
    assert row["result_sha256"] == hashlib.sha256(serialized(payload)).hexdigest()


@pytest.mark.parametrize("size,truncated", [(65536, False), (65537, True)])
def test_result_limit_is_strictly_greater_than_the_threshold(v3_tx, monkeypatch, size, truncated):
    payload = ["x" * (size - 4)]  # JSON brackets and quotes add four bytes.
    assert len(serialized(payload)) == size
    patch_result(monkeypatch, payload)
    env = make_tool_env(max_bytes=65536)
    envelope = dispatch.call(env, "get_account", {})
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert envelope["data"] == payload
    assert row["truncated"] is truncated
    assert row["result"] == ({"truncated": True, "bytes": size} if truncated else payload)


def test_unserializable_result_is_recorded_as_error_and_next_call_succeeds(v3_tx, monkeypatch):
    patch_result(monkeypatch, {"unsupported": object()})
    env = make_tool_env()
    envelope = dispatch.call(env, "get_account", {})
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert envelope["ref"] == "call:1"
    assert envelope["error"].startswith("invalid_argument: ")
    assert "not JSON serializable" in envelope["error"]
    assert row["result"] == {"error": envelope["error"]}
    assert row["result_sha256"] is None
    assert row["truncated"] is False
    patch_result(monkeypatch, [])
    assert dispatch.call(env, "get_account", {}) == {"ref": "call:2", "data": []}
    row = facts_repo.tool_call(v3_tx, env.agent_session, 2)
    assert row is not None
    assert row["result"] == []


def test_oversized_error_detail_is_truncated_only_in_db(v3_tx, monkeypatch):
    detail = {"reason": "数" * 24000}

    def reject(env):
        raise ToolError("active_plan_exists", detail=detail)

    monkeypatch.setitem(dispatch._BY_NAME, "get_account", replace(dispatch._BY_NAME["get_account"], fn=reject))
    env = make_tool_env(max_bytes=65536)
    envelope = dispatch.call(env, "get_account", {})
    result = {"error": "active_plan_exists", "detail": detail}
    assert envelope == {"ref": "call:1", **result}
    row = facts_repo.tool_call(v3_tx, env.agent_session, 1)
    assert row is not None
    assert row["result"] == {"truncated": True, "bytes": len(serialized(result))}
    assert row["truncated"] is True
    assert row["result_sha256"] is None


def test_record_failure_does_not_change_the_returned_payload(v3_tx):
    def unavailable(work):
        raise RuntimeError("audit unavailable")

    env = replace(make_tool_env(), write=unavailable)
    envelope = dispatch.call(env, "get_account", {})
    assert envelope["ref"] == "call:0"
    assert "data" in envelope
    assert facts_repo.tool_call(v3_tx, env.agent_session, 1) is None
