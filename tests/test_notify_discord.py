import contextlib
from unittest.mock import MagicMock, patch

import pytest

from investment_assistant.notify.discord import DiscordChannel, DiscordClient


@contextlib.contextmanager
def patched_post():
    """The webhook client posts through fetch-1's policy_session; swap in a session whose post() is a mock."""
    session = MagicMock()
    session.__enter__.return_value = session
    with patch("investment_assistant.notify.discord.fetch_policy.policy_session", return_value=session):
        yield session.post


def test_send_earnings_calls_correct_webhook():
    client = DiscordClient(
        earnings_url="https://discord.com/api/webhooks/test/earnings",
        signals_url="https://discord.com/api/webhooks/test/signals",
    )
    with patched_post() as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        client.send(DiscordChannel.EARNINGS, {"content": "test"})
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "earnings" in call_url


def test_send_raises_on_non_204():
    client = DiscordClient(
        earnings_url="https://discord.com/api/webhooks/test/earnings",
        signals_url="",
    )
    with patched_post() as mock_post:
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        with pytest.raises(RuntimeError, match="Discord send failed"):
            client.send(DiscordChannel.EARNINGS, {"content": "test"})


def test_send_signals_calls_signals_webhook():
    client = DiscordClient(
        earnings_url="https://discord.com/api/webhooks/test/earnings",
        signals_url="https://discord.com/api/webhooks/test/signals",
    )
    with patched_post() as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        client.send(DiscordChannel.SIGNALS, {"content": "test"})
        call_url = mock_post.call_args[0][0]
        assert "signals" in call_url


def test_render_discord_matches_embed_shape():
    from investment_assistant.notify.discord import render_discord
    from investment_assistant.notify.message import Notification, NotifyField

    n = Notification(
        title="📄 财报 · 2026-07-03",
        color=15844367,
        fields=[NotifyField("新提交 (1)", "• **TEST** 10-Q — 2026-07-03")],
        footer="earning-agent • 2026-07-03",
    )
    payload = render_discord(n)
    embed = payload["embeds"][0]
    assert embed["title"] == "📄 财报 · 2026-07-03"
    assert embed["color"] == 15844367
    assert embed["fields"] == [{"name": "新提交 (1)", "value": "• **TEST** 10-Q — 2026-07-03", "inline": False}]
    assert embed["footer"] == {"text": "earning-agent • 2026-07-03"}


def test_discord_channels():
    from investment_assistant.notify.discord import DiscordChannel

    assert [c.value for c in DiscordChannel] == ["earnings", "signals"]


def test_chunk_splits_by_lines_and_chars():
    from investment_assistant.notify.discord import chunk

    # 3 行各 1000 字：两行拼一起就超限，只能一行一块 → 3 块
    three_lines = "\n".join("x" * 1000 for _ in range(3))
    assert [len(part) for part in chunk(three_lines)] == [1000, 1000, 1000]
    # 单行 4000 字：按字符切 → 1900 + 1900 + 200
    one_long_line = "y" * 4000
    assert [len(part) for part in chunk(one_long_line)] == [1900, 1900, 200]
    # 短文本不切；行边界保留
    assert chunk("a\nb\nc") == ["a\nb\nc"]
    assert chunk("") == []


def test_send_text_posts_each_chunk():
    client = DiscordClient(
        earnings_url="https://discord.com/api/webhooks/test/earnings",
        signals_url="https://discord.com/api/webhooks/test/signals",
    )
    text = "\n".join("z" * 1000 for _ in range(3))
    with patched_post() as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        client.send_text(DiscordChannel.SIGNALS, text)
        assert mock_post.call_count == 3
        payloads = [call.kwargs["json"] for call in mock_post.call_args_list]
        assert all(set(p) == {"content"} for p in payloads)
        assert "".join(p["content"].replace("\n", "") for p in payloads) == "z" * 3000
