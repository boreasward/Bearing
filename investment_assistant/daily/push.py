"""The Discord push of the daily brief (spec §5 步骤 5、§5.4、§11): the summary lines go to the
signals channel in chunks; the error day pushes the reason-only notice; the weekly report is
its own single message."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from investment_assistant.data import collect as data_collect
from investment_assistant.facts.brief import discord_lines, render_error_brief
from investment_assistant.notify.discord import DiscordChannel


def push_brief(view: dict, *, brief_path: Path, client) -> None:
    """The final brief's summary lines, chunked by the client (v3 设计验收 7)."""
    text = "\n".join(discord_lines(view, brief_path=str(brief_path)))
    client.send_text(DiscordChannel.SIGNALS, text)


def push_weekly(markdown: str, client) -> None:
    """周报单独一条（spec §5.4），与简报一样发到信号频道；分条由 client 自己切。"""
    client.send_text(DiscordChannel.SIGNALS, markdown)


def push_error(session: date, error: str, has_holdings: bool, client, *, now: datetime | None = None) -> None:
    """The error-day notice; its first line comes from render_error_brief — with holdings that
    is 「今天无法检查止损：…」(spec §5), never rewritten here."""
    now = now if now is not None else data_collect.wall_clock()
    rendered = render_error_brief(session=session, as_of=now, error=error, has_holdings=has_holdings)
    lead = next(line for line in rendered.splitlines() if line.strip() and not line.startswith("#"))
    client.send_text(DiscordChannel.SIGNALS, lead)
