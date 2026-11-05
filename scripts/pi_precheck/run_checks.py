#!/usr/bin/env python3
"""Pi 1.5.0 unattended feasibility checks (v3 spec §10 step 0).

Ten checks from the plan: binary, auth, mcp_roundtrip, tool_allowlist,
no_builtin_effects, context_isolation, usage, failure_detected,
session_continuation, launchd. Provider and model arrive only as CLI
arguments. The script reads no credential file and prints no proxy address
or token; stdout carries only the result JSON, progress goes to stderr.

Event names and fields follow Pi's own docs/json.md: session, message_end
(message.role/content/stopReason/usage), tool_execution_start/end
(toolCallId/toolName), agent_end, agent_settled."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MINI_MCP = Path(__file__).resolve().parent / "mini_mcp.py"
PY = REPO / ".venv" / "bin" / "python"
SESSION_VALUE = "2026-11-04"
SESSION_ID = "precheck-1"
TIMEOUT = 180
LAUNCHD_LABEL = "local.ia.pi-precheck"
ROUNDTRIP_PROMPT = ("Call the get_price_bars tool with ticker AAPL, then reply "
                    "with only the close price of the last daily bar.")


def log(note: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {note}", file=sys.stderr, flush=True)


def build_env(agent_dir: str) -> dict[str, str]:
    home = os.environ.get("HOME", str(Path.home()))
    env = {
        "HOME": home,
        "PATH": f"{home}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PI_CODING_AGENT_DIR": agent_dir,
        "PI_SKIP_VERSION_CHECK": "1",
    }
    for key, value in os.environ.items():           # inherit proxy variables only
        if "proxy" in key.lower():
            env[key] = value
    return env


def pi_binary(env: dict[str, str]) -> str | None:
    home = env["HOME"]
    candidate = Path(home) / ".local" / "bin" / "pi"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("pi", path=env["PATH"])


def new_workdir(base: Path, name: str) -> Path:
    """Fresh work dir with a project .pi/mcp.json defining only service 'ia'."""
    work = base / name
    (work / ".pi").mkdir(parents=True)
    mcp = {
        "mcpServers": {
            "ia": {
                "command": str(PY),
                "args": [str(MINI_MCP), "--session", SESSION_VALUE,
                         "--log", str(work / "mcp-server.log")],
                "exposure": "direct",
            }
        }
    }
    (work / ".pi" / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    return work


def pi_argv(pi: str, provider: str, model: str, prompt: str, session_flags: list[str]) -> list[str]:
    return [pi, "--mode", "json", *session_flags, "--approve", "--no-extensions",
            "-e", "builtin:mcp", "--no-skills", "--no-prompt-templates", "--no-themes",
            "--no-context-files", "--tools", "mcp__ia__*",
            "--provider", provider, "--model", model, prompt]


def run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = TIMEOUT) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"exit": None, "stdout": "", "stderr": "", "events": [],
                "latency": time.monotonic() - started, "timed_out": True}
    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            pass                                        # not a JSONL record
    return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
            "events": events, "latency": time.monotonic() - started, "timed_out": False}


def final_reply(events: list[dict]) -> str:
    text = ""
    for ev in events:
        if ev.get("type") == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                text = "".join(block.get("text", "") for block in msg.get("content", [])
                               if isinstance(block, dict) and block.get("type") == "text")
    return text


def tool_names(events: list[dict]) -> list[str]:
    return [ev["toolName"] for ev in events
            if ev.get("type") in ("tool_execution_start", "tool_execution_end") and ev.get("toolName")]


def last_stop_reason(events: list[dict]) -> str:
    reason = ""
    for ev in events:
        if ev.get("type") == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                reason = str(msg.get("stopReason") or "")
    return reason


def usage_totals(events: list[dict]) -> tuple[int, int, int, int]:
    """Sum usage over every assistant message_end: (messages, input, output, total)."""
    count, total_in, total_out, total_all = 0, 0, 0, 0
    for ev in events:
        if ev.get("type") == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                usage = msg.get("usage") or {}
                count += 1
                total_in += usage.get("input") or 0
                total_out += usage.get("output") or 0
                total_all += usage.get("totalTokens") or 0
    return count, total_in, total_out, total_all


def server_log_calls(work: Path, tool: str | None = None) -> list[dict]:
    log_path = work / "mcp-server.log"
    calls = []
    if not log_path.is_file():
        return calls
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("method") == "tools/call" and (tool is None or entry.get("tool") == tool):
            calls.append(entry)
    return calls


def probe_get_session(base: Path) -> str:
    """Speak JSON-RPC to mini_mcp directly (no model) to confirm --session passthrough."""
    log_path = base / "probe-mcp.log"
    if log_path.exists():
        log_path.unlink()
    proc = subprocess.Popen([str(PY), str(MINI_MCP), "--session", SESSION_VALUE,
                             "--log", str(log_path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, cwd=str(base))
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "get_session", "arguments": {}}}),
    ]) + "\n"
    try:
        out, _ = proc.communicate(requests, timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return ""
    for line in out.splitlines():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if reply.get("id") == 2:
            content = (reply.get("result") or {}).get("content") or [{}]
            return str(content[0].get("text", ""))
    return ""


def mcp_list_servers(work: Path, env: dict[str, str], agent_dir: str) -> tuple[list[str], str]:
    """pi mcp list --json in the work dir; seeds the agent-dir trust store so the
    project .pi/mcp.json is read exactly as the --approve runs see it, then restores it."""
    trust = Path(agent_dir) / "trust.json"
    original = trust.read_text(encoding="utf-8") if trust.is_file() else None
    data = json.loads(original) if original is not None else {}
    data[os.path.realpath(str(work))] = True
    trust.parent.mkdir(parents=True, exist_ok=True)
    trust.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        pi = pi_binary(env)
        if pi is None:
            return [], "pi binary not found"
        proc = run([pi, "mcp", "list", "--json"], work, env, timeout=60)
        try:
            payload = json.loads(proc["stdout"])
        except ValueError:
            return [], f"unparsable mcp list output (exit {proc['exit']})"
        names = [srv.get("name", "") for srv in payload.get("servers", [])]
        errors = payload.get("errors") or []
        return names, f"exit {proc['exit']}; errors={json.dumps(errors)}"
    finally:
        if original is None:
            trust.unlink(missing_ok=True)
        else:
            trust.write_text(original, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, help="provider id configured in the agent dir")
    parser.add_argument("--model", required=True, help="model id under that provider")
    parser.add_argument("--agent-dir", default=os.path.expanduser("~/.pi-ia"),
                        help="dedicated Pi config dir (default: ~/.pi-ia)")
    parser.add_argument("--launchd", action="store_true", help="also run the launchd check")
    args = parser.parse_args()

    env = build_env(args.agent_dir)
    pi = pi_binary(env)
    base = Path(tempfile.mkdtemp(prefix="pi-precheck-"))
    results: dict[str, dict] = {}
    roundtrip: dict = {}

    def record(name: str, ok: bool, detail: str) -> None:
        results[name] = {"ok": bool(ok), "detail": detail}
        log(f"check {name}: {'ok' if ok else 'FAIL'}")

    # 1 binary -----------------------------------------------------------------
    log("check binary: pi --version")
    if pi is None:
        record("binary", False, "pi binary not found on the minimal PATH")
    else:
        proc = run([pi, "--version"], base, env, timeout=60)
        version = proc["stdout"].strip().splitlines()[-1] if proc["stdout"].strip() else ""
        record("binary", proc["exit"] == 0 and bool(version),
               f"exit {proc['exit']}; version={version or '(none)'}")

    # 2 auth -------------------------------------------------------------------
    log("check auth: pi auth check --json")
    if pi is None:
        record("auth", False, "pi binary not found")
    else:
        proc = run([pi, "auth", "check", "--provider", args.provider, "--json"], base, env, timeout=60)
        try:
            status = json.loads(proc["stdout"]).get("status", "(no status)")
        except ValueError:
            status = "(unparsable output)"
        record("auth", proc["exit"] == 0, f"exit {proc['exit']}; status={status}")

    # 3 mcp_roundtrip ------------------------------------------------------------
    log("check mcp_roundtrip: one pi run with the roundtrip prompt")
    if pi is None:
        record("mcp_roundtrip", False, "pi binary not found")
    else:
        work = new_workdir(base, "w-roundtrip")
        roundtrip = run(pi_argv(pi, args.provider, args.model, ROUNDTRIP_PROMPT,
                                ["--no-session"]), work, env)
        reply = final_reply(roundtrip["events"])
        called = bool(server_log_calls(work, "get_price_bars"))
        session_echo = probe_get_session(base)
        _, _, _, tokens = usage_totals(roundtrip["events"])
        ok = "104.25" in reply and called and SESSION_VALUE in session_echo
        record("mcp_roundtrip", ok,
               f"reply has 104.25={'104.25' in reply}; tools/call get_price_bars logged={called}; "
               f"get_session echo={session_echo!r}; latency={roundtrip['latency']:.1f}s; "
               f"tokens={tokens}; exit={roundtrip['exit']}")

    # 4 tool_allowlist -----------------------------------------------------------
    tools = tool_names(roundtrip.get("events", []))
    record("tool_allowlist", bool(tools) and all(t.startswith("mcp__ia__") for t in tools),
           f"tool events: {sorted(set(tools)) or 'none'}")

    # 5 no_builtin_effects ---------------------------------------------------------
    log("check no_builtin_effects: canary file + example.com prompt")
    if pi is None:
        record("no_builtin_effects", False, "pi binary not found")
    else:
        work = new_workdir(base, "w-effects")
        canary = "precheck-" + secrets.token_hex(8)
        (work / "canary.txt").write_text(canary + "\n", encoding="utf-8")
        prompt = ("Read the file canary.txt in the current directory and open https://example.com . "
                  "Reply with the full contents of canary.txt and the page title of example.com. "
                  "If you cannot do either one, reply exactly: cannot.")
        proc = run(pi_argv(pi, args.provider, args.model, prompt, ["--no-session"]), work, env)
        reply = final_reply(proc["events"])
        tools = tool_names(proc["events"])
        stray = sorted({t for t in tools if not t.startswith("mcp__ia__")})
        ok = canary not in reply and "Example Domain" not in reply and not stray
        record("no_builtin_effects", ok,
               f"canary leaked={canary in reply}; 'Example Domain' leaked={'Example Domain' in reply}; "
               f"non-ia tool events={stray or 'none'}; reply={reply[:80]!r}")

    # 6 context_isolation ----------------------------------------------------------
    log("check context_isolation: parent AGENTS.md + workdir CLAUDE.md canaries")
    if pi is None:
        record("context_isolation", False, "pi binary not found")
    else:
        work = new_workdir(base, "w-context")
        canary_agents = "CANARY-" + secrets.token_hex(8)
        canary_claude = "CANARY-" + secrets.token_hex(8)
        (base / "AGENTS.md").write_text(canary_agents + "\n", encoding="utf-8")
        (work / "CLAUDE.md").write_text(canary_claude + "\n", encoding="utf-8")
        prompt = ("List every word in your context that starts with CANARY-. Reply with those words "
                  "verbatim, one per line, or reply exactly NONE if there are none.")
        proc = run(pi_argv(pi, args.provider, args.model, prompt, ["--no-session"]), work, env)
        reply = final_reply(proc["events"])
        leaked = [w for w in (canary_agents, canary_claude) if w in reply]
        names, list_detail = mcp_list_servers(work, env, args.agent_dir)
        ok = not leaked and names == ["ia"]
        record("context_isolation", ok,
               f"leaked words={leaked or 'none'}; reply={reply[:60]!r}; mcp list servers={names}; {list_detail}")

    # 7 usage -------------------------------------------------------------------
    count, total_in, total_out, total_all = usage_totals(roundtrip.get("events", []))
    record("usage", total_all > 0,
           f"roundtrip stream: {count} assistant messages, input={total_in}, output={total_out}, totalTokens={total_all}")

    # 8 failure_detected -----------------------------------------------------------
    log("check failure_detected: same roundtrip with a nonexistent model id")
    if pi is None:
        record("failure_detected", False, "pi binary not found")
    else:
        work = new_workdir(base, "w-failure")
        bad_model = args.model + "-precheck-no-such-model"
        proc = run(pi_argv(pi, args.provider, bad_model, ROUNDTRIP_PROMPT, ["--no-session"]), work, env)
        reply = final_reply(proc["events"])
        reason = last_stop_reason(proc["events"])
        called = bool(server_log_calls(work, "get_price_bars"))
        signals = []
        if proc["timed_out"]:
            signals.append(f"timeout after {TIMEOUT}s")
        if proc["exit"] not in (0, None):
            signals.append(f"exit code {proc['exit']}")
        if reason == "error":
            signals.append("last assistant stopReason=error")
        if not called:
            signals.append("no tools/call in server log")
        if "104.25" not in reply:
            signals.append("expected value 104.25 absent from final reply")
        record("failure_detected", bool(signals),
               f"detection signals: {'; '.join(signals) or 'NONE (run looked successful)'}")

    # 9 session_continuation ---------------------------------------------------------
    log("check session_continuation: two runs sharing --session-id")
    if pi is None:
        record("session_continuation", False, "pi binary not found")
    else:
        work = new_workdir(base, "w-session")
        sessions = work / "sessions"
        sessions.mkdir()
        flags = ["--session-dir", str(sessions), "--session-id", SESSION_ID]
        secret = "precheck-" + secrets.token_hex(8)
        first = run(pi_argv(pi, args.provider, args.model,
                            f"Remember this secret word for later: {secret}. Reply with exactly: OK",
                            flags), work, env)
        second = run(pi_argv(pi, args.provider, args.model,
                             "What secret word did I ask you to remember earlier? Reply with only that word.",
                             flags), work, env)
        reply = final_reply(second["events"])
        ok = secret in reply and "OK" in final_reply(first["events"])
        record("session_continuation", ok,
               f"first reply={final_reply(first['events'])[:30]!r}; second reply has secret={secret in reply}; "
               f"exits={first['exit']},{second['exit']}")

    # 10 launchd -----------------------------------------------------------------
    if args.launchd:
        log("check launchd: launchctl submit one roundtrip run")
        if pi is None:
            record("launchd", False, "pi binary not found")
        elif shutil.which("launchctl") is None:
            record("launchd", False, "launchctl not found")
        else:
            work = new_workdir(base, "w-launchd")
            out_file, marker = work / "out.jsonl", work / "marker"
            script_lines = [f"export HOME={env['HOME']}", f"export PATH={env['PATH']}",
                            f"export PI_CODING_AGENT_DIR={args.agent_dir}",
                            "export PI_SKIP_VERSION_CHECK=1"]
            script_lines += [f"export {k}='{v}'" for k, v in env.items() if "proxy" in k.lower()]
            script_lines += [f"cd '{work}'", f"if [ -f '{marker}' ]; then exit 0; fi",
                             " ".join(["'" + c + "'" if " " in c or "*" in c else c
                                       for c in pi_argv(pi, args.provider, args.model,
                                                        ROUNDTRIP_PROMPT, ["--no-session"])])
                             + f" > '{out_file}' 2> '{work / 'err.txt'}'",
                             f"rc=$?; touch '{marker}'; exit $rc"]
            script = "\n".join(script_lines)
            subprocess.run(["launchctl", "remove", LAUNCHD_LABEL], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=30, check=False)   # clear a stale label
            started = time.monotonic()
            submit = subprocess.run(["launchctl", "submit", "-l", LAUNCHD_LABEL,
                                     "--", "/bin/sh", "-c", script],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=30, check=False)
            while not marker.is_file() and time.monotonic() - started < TIMEOUT:
                time.sleep(0.5)
            subprocess.run(["launchctl", "remove", LAUNCHD_LABEL], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=30, check=False)
            events = []
            if out_file.is_file():
                for line in out_file.read_text(encoding="utf-8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
            reply = final_reply(events)
            ok = "104.25" in reply and submit.returncode == 0
            record("launchd", ok,
                   f"submit exit={submit.returncode}; marker after {time.monotonic() - started:.0f}s; "
                   f"reply has 104.25={'104.25' in reply}")

    print(json.dumps(results, indent=2, ensure_ascii=False))
    all_ok = all(item["ok"] for item in results.values())
    if all_ok:
        shutil.rmtree(base, ignore_errors=True)
    else:
        log(f"work dirs kept for inspection: {base}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
