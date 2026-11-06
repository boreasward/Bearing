from investment_assistant.notify.discord import DiscordChannel, DiscordClient


def test_from_config_prefers_config_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_EARNINGS", "env-earnings")
    monkeypatch.setenv("DISCORD_WEBHOOK_SIGNALS", "env-signals")
    from investment_assistant.config import NotifyConfig
    cfg = NotifyConfig(webhooks={"signals": "cfg-signals"})
    client = DiscordClient.from_config(cfg)
    assert client._urls[DiscordChannel.SIGNALS] == "cfg-signals"
    assert client._urls[DiscordChannel.EARNINGS] == "env-earnings"
