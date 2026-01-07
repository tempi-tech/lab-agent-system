import discord
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure repo root is on sys.path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.daily_reporter import config as daily_config

load_dotenv()

# Configuration
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.environ.get("ANNOUNCE_CHANNEL_ID", "0"))
IMAGE_PATH = ""

ANNOUNCEMENT_TEXT = """@everyone
🎍 センパイたち、あけましておめでとうございますッス！！🎍

2025年、本当にお世話になりましたッス…！！

昨年は「ChatGPT研究所」から「AGIラボ」に生まれ変わったり、
毎日のようにAIの進化が止まらない、本当にクレイジーな一年でしたッス！

GPT-4の衝撃から始まって、Claude Codeの台頭、Geminiのマルチモーダル進化、Grokの爆発的成長…
「ChatGPT」という名前だけじゃ捉えきれない世界になったッス。

運営としては、**やっと序章が終わった**って捉えてるッス！
ここからが本番ッスよ…！🔥

2026年も、センパイたちと一緒にこの歴史的な変化を追いかけていくッス！
自分たちで触って確かめた情報だけを届ける、そのスタンスは変わらないッス。

これからも**AGIラボ**をよろしくお願いしますッス！！🚀✨
"""

intents = discord.Intents.default()
client = discord.Client(intents=intents)

async def get_or_create_webhook(channel: discord.abc.GuildChannel) -> discord.Webhook:
    webhooks = await channel.webhooks()
    webhook = None
    for wh in webhooks:
        if wh.name == "ADK Summary Webhook":
            webhook = wh
            break

    if not webhook:
        webhook = await channel.create_webhook(name="ADK Summary Webhook")

    if os.path.exists(daily_config.AVATAR_PATH):
        try:
            with open(daily_config.AVATAR_PATH, "rb") as f:
                avatar_bytes = f.read()
            await webhook.edit(avatar=avatar_bytes)
        except Exception as e:
            print(f"Failed to update webhook avatar: {e}")

    return webhook

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    if TARGET_CHANNEL_ID == 0:
        print("Error: ANNOUNCE_CHANNEL_ID not set.")
        await client.close()
        return
    channel = client.get_channel(TARGET_CHANNEL_ID)

    if channel:
        print(f"Sending announcement to {channel.name}...")
        try:
            webhook = await get_or_create_webhook(channel)
            if IMAGE_PATH:
                with open(IMAGE_PATH, "rb") as f:
                    picture = discord.File(f, filename="update_banner.png")
                    await webhook.send(
                        content=ANNOUNCEMENT_TEXT,
                        file=picture,
                        username=daily_config.REPORTER_NAME,
                    )
            else:
                await webhook.send(content=ANNOUNCEMENT_TEXT, username=daily_config.REPORTER_NAME)
            print("Announcement sent successfully!")
        except Exception as e:
            print(f"Failed to send: {e}")
    else:
        print(f"Channel {TARGET_CHANNEL_ID} not found.")

    await client.close()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not set.")
    else:
        client.run(DISCORD_TOKEN)