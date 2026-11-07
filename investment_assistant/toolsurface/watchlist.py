"""Watchlist write tools (spec §10 关注列表增删): chat-only confirmed writes straight to
``watchlist_items`` through the recovered db functions of commit d082400."""

from __future__ import annotations

from investment_assistant import db
from investment_assistant.toolsurface.env import ToolEnv
from investment_assistant.toolsurface.validation import confirmed_flag, normalize_ticker, note_text, text_arg


def watchlist_add(env: ToolEnv, *, ticker: str, request_id: str, confirmed, thesis: str = "") -> dict:
    """把一只股票加进关注列表（可带论点）；先复述，confirmed 为真才写。"""
    ticker = normalize_ticker(ticker)
    request_id = text_arg(request_id, "request_id")
    thesis_text = note_text(thesis)
    if not confirmed_flag(confirmed):
        suffix = f"（论点：{thesis_text}）" if thesis_text else ""
        return {"write": False, "preview": f"将把 {ticker} 加入关注列表{suffix}。确认后写入。"}

    def _work(conn):
        item = db.upsert_watchlist_item(conn, ticker=ticker, thesis=thesis_text or None)
        return {"item": item}

    return env.write(_work)


def watchlist_remove(env: ToolEnv, *, ticker: str, request_id: str, confirmed) -> dict:
    """把一只股票移出关注列表；先复述，confirmed 为真才写。"""
    ticker = normalize_ticker(ticker)
    request_id = text_arg(request_id, "request_id")
    if not confirmed_flag(confirmed):
        return {"write": False, "preview": f"将把 {ticker} 从关注列表移除。确认后写入。"}
    return env.write(lambda conn: db.delete_watchlist_item(conn, ticker))
