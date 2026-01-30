import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

# Load env first so daily_reporter config picks it up.
load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.daily_reporter.logic import DailyReporterAgent


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env: {name}")
    return value


def _format_channel_location(channel: discord.abc.GuildChannel) -> str:
    if hasattr(channel, "parent") and channel.parent:
        return f"{channel.parent.name} > {channel.name}"
    return channel.name


def main() -> None:
    _require_env("DISCORD_TOKEN")

    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(intents=intents)
    reporter = DailyReporterAgent()

    @client.event
    async def on_ready():
        await reporter.on_ready(client)

        channels = reporter.resolve_source_channels()
        print(f"Resolved {len(channels)} channels")
        for channel in channels:
            location = _format_channel_location(channel)
            print(f"- {location} ({channel.id})")

        target_id = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if target_id:
            print(f"Target channel: {target_id}")

        await client.close()

    client.run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
