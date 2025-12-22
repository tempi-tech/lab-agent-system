import os
import discord
from dotenv import load_dotenv

# Load env first so daily_reporter config picks it up.
load_dotenv()

TEST_CHANNEL_ID = int(os.getenv("SMOKE_TEST_CHANNEL_ID", "0"))
os.environ["DISCORD_CHANNEL_ID"] = str(TEST_CHANNEL_ID)
os.environ["SOURCE_CHANNEL_IDS"] = str(TEST_CHANNEL_ID)

from src.agents.daily_reporter.logic import DailyReporterAgent


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env: {name}")
    return value


def main() -> None:
    _require_env("DISCORD_TOKEN")
    _require_env("GOOGLE_API_KEY")
    if TEST_CHANNEL_ID == 0:
        raise RuntimeError("Missing env: SMOKE_TEST_CHANNEL_ID")

    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(intents=intents)
    reporter = DailyReporterAgent()

    @client.event
    async def on_ready():
        channel = client.get_channel(TEST_CHANNEL_ID)
        if channel is None:
            print(f"Smoke test channel not found: {TEST_CHANNEL_ID}")
            await client.close()
            return

        await reporter.on_ready(client)

        await channel.send("🧪 Smoke test: start (daily_reporter)")
        await reporter.generate_summary(channel)
        await channel.send("✅ Smoke test: done")

        await client.close()

    client.run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
