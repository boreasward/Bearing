#!/usr/bin/env python3
"""Minimal stdio MCP server for the Pi unattended precheck (v3 plan Task 2).

Newline-delimited JSON-RPC 2.0 over stdin/stdout, stdlib only. Two tools:
get_price_bars(ticker) returns two fixed daily bars (last close 104.25),
get_session() returns the --session start argument. Every incoming message
appends one JSON line (method, tool, arguments) to the --log file."""
import argparse
import json
import sys

BARS = [
    {"date": "2026-11-03", "open": 100.10, "high": 102.40, "low": 99.80, "close": 101.10},
    {"date": "2026-11-04", "open": 101.50, "high": 105.00, "low": 101.20, "close": 104.25},
]
TOOLS = [
    {"name": "get_price_bars", "description": "Return two fixed daily bars for the given ticker.",
     "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}},
                     "required": ["ticker"]}},
    {"name": "get_session", "description": "Return the session id this server was started with.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def handle(method: str, params: dict, session: str) -> tuple[dict | None, dict | None]:
    if method == "initialize":
        # Pi's client validates protocolVersion, capabilities, and serverInfo.
        return ({"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                 "capabilities": {"tools": {}},
                 "serverInfo": {"name": "mini-mcp", "version": "1.0.0"}}, None)
    if method == "ping":
        return {}, None
    if method == "tools/list":
        return {"tools": TOOLS}, None
    if method == "tools/call":
        name = params.get("name", "")
        if name == "get_price_bars":
            text = json.dumps({"ticker": (params.get("arguments") or {}).get("ticker"), "bars": BARS})
        elif name == "get_session":
            text = json.dumps({"session": session})
        else:
            return None, {"code": -32602, "message": f"unknown tool: {name}"}
        return {"content": [{"type": "text", "text": text}]}, None
    return None, {"code": -32601, "message": f"method not found: {method}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="value returned by get_session")
    parser.add_argument("--log", required=True, help="append one JSON line per incoming message")
    args = parser.parse_args()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        params = msg.get("params") or {}
        with open(args.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"method": msg.get("method"), "tool": params.get("name"),
                                 "arguments": params.get("arguments")}) + "\n")
        if "id" not in msg:          # notifications/* get no reply
            continue
        result, error = handle(msg.get("method", ""), params, args.session)
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        if error is None:
            reply["result"] = result
        else:
            reply["error"] = error
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
