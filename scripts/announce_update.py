import discord
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.environ.get("ANNOUNCE_CHANNEL_ID", "0"))
IMAGE_PATH = ""

ANNOUNCEMENT_TEXT = """@everyone
**【お知らせ】コンテストフォーラム、始動！**

新しく「コンテスト」フォーラムを作成しました。
毎日お題を投稿し、LLMが採点してくれます笑

本日のお題はこちら！
https://discord.com/channels/GUILD_ID/CHANNEL_ID

コンテストフォーラム（#コンテスト）：
https://discord.com/channels/GUILD_ID/CHANNEL_ID

⏰ 回答締切：本日 22:00
"""

intents = discord.Intents.default()
client = discord.Client(intents=intents)

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
            if IMAGE_PATH:
                with open(IMAGE_PATH, 'rb') as f:
                    picture = discord.File(f, filename="update_banner.png")
                    await channel.send(content=ANNOUNCEMENT_TEXT, file=picture)
            else:
                await channel.send(content=ANNOUNCEMENT_TEXT)
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
