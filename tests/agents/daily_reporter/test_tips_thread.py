from src.agents.daily_reporter.logic import DailyReporterAgent


def test_format_tips_for_thread_sanitizes_external_links_and_keeps_discord_url_field():
    agent = DailyReporterAgent()
    raw_tips = """
TIPS_START
概要: Try this https://evil.example and @everyone now
○ Step with http://phish.example
URL: https://discord.com/channels/1/2/3
TIPS_END
""".strip()

    formatted = agent.format_tips_for_thread(raw_tips)

    assert "https://evil.example" not in formatted
    assert "http://phish.example" not in formatted
    assert "[外部リンク]" in formatted
    assert "📎 https://discord.com/channels/1/2/3" in formatted


import asyncio


def test_post_tips_thread_disables_mentions(monkeypatch):
    class DummyMainMessage:
        async def create_thread(self, name, auto_archive_duration):
            class DummyThread:
                name = "tips"

            return DummyThread()

    class DummyWebhook:
        def __init__(self):
            self.kwargs = None

        async def send(self, **kwargs):
            self.kwargs = kwargs

    agent = DailyReporterAgent()
    webhook = DummyWebhook()
    main_message = DummyMainMessage()
    tips = "TIPS_START\n概要: @everyone test\nTIPS_END"

    asyncio.run(agent.post_tips_thread(main_message, tips, webhook))

    assert webhook.kwargs is not None
    assert webhook.kwargs["allowed_mentions"].everyone is False
    assert webhook.kwargs["allowed_mentions"].users is False
    assert webhook.kwargs["allowed_mentions"].roles is False
