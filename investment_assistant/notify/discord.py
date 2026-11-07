import time
from collections.abc import Mapping
from enum import Enum

from investment_assistant.data import fetch_policy
from investment_assistant.notify.message import Notification

# fetch-1 (D23): a push is not idempotent, so it is retried only when the request never left (connection failure)
# or on 429 (Retry-After capped at 8 s); a read timeout or a 5xx is never resent.
PUSH_POLICY = fetch_policy.FetchPolicy(retry_post=False)


class DiscordChannel(Enum):
    EARNINGS = "earnings"
    SIGNALS = "signals"


class DiscordClient:
    def __init__(
        self, earnings_url: str, signals_url: str, *, policy: fetch_policy.FetchPolicy = PUSH_POLICY, sleep=time.sleep
    ):
        self._urls = {
            DiscordChannel.EARNINGS: earnings_url,
            DiscordChannel.SIGNALS: signals_url,
        }
        self._policy = policy
        self._sleep = sleep

    def send(self, channel: DiscordChannel, payload: dict) -> None:
        url = self._urls[channel]

        def attempt() -> None:
            with fetch_policy.policy_session(self._policy) as session:
                resp = session.post(url, json=payload)
            if resp.status_code not in (200, 204):
                headers = getattr(resp, "headers", None)
                retry_after = headers.get("Retry-After") if isinstance(headers, Mapping) else None
                raise fetch_policy.http_error(
                    resp.status_code, f"Discord send failed: {resp.status_code} {resp.text}", retry_after=retry_after
                )

        fetch_policy.fetch(f"discord:{channel.value}", attempt, policy=self._policy, sleep=self._sleep)

    def send_text(self, channel: DiscordChannel, text: str) -> None:
        """Post a long text in chunks of at most `limit` characters, one message each."""
        for part in chunk(text):
            self.send(channel, {"content": part})

    @classmethod
    def from_env(cls) -> "DiscordClient":
        import os

        from dotenv import load_dotenv

        load_dotenv()
        return cls(
            earnings_url=os.environ["DISCORD_WEBHOOK_EARNINGS"],
            signals_url=os.environ["DISCORD_WEBHOOK_SIGNALS"],
        )

    @classmethod
    def from_config(cls, notify_cfg) -> "DiscordClient":
        import os

        from dotenv import load_dotenv

        load_dotenv()

        def url(channel: str, env_key: str) -> str:
            return notify_cfg.webhooks.get(channel) or os.environ.get(env_key, "")

        return cls(
            earnings_url=url("earnings", "DISCORD_WEBHOOK_EARNINGS"),
            signals_url=url("signals", "DISCORD_WEBHOOK_SIGNALS"),
        )


def chunk(text: str, *, limit: int = 1900) -> list[str]:
    """Split `text` into Discord-sized blocks without breaking lines when possible;
    a single line longer than `limit` is split by characters (v3 设计验收 7)."""
    blocks: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            blocks.append("\n".join(current))
            current, length = [], 0

    for line in text.split("\n"):
        while len(line) > limit:
            flush()
            blocks.append(line[:limit])
            line = line[limit:]
        if current and length + 1 + len(line) > limit:
            flush()
        length += len(line) + (1 if current else 0)
        current.append(line)
    flush()
    return [block for block in blocks if block != ""]


def render_discord(notification: Notification) -> dict:
    return {
        "embeds": [
            {
                "title": notification.title,
                "color": notification.color,
                "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in notification.fields],
                "footer": {"text": notification.footer},
            }
        ]
    }
