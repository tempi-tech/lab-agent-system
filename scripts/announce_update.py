import discord
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
TARGET_CHANNEL_ID = 842348486234341407  # random
IMAGE_PATH = "/Users/kai/Develop/autogen/lab-agent-system/scripts/geminiupdate.png"

ANNOUNCEMENT_TEXT = """@everyone
**【お知らせ】ラボちゃんのアップデート**

日々の活動を要約するAIエージェント「ラボちゃん」に、以下の改善を行いました。

**🆕 アップデート内容:**

**🧠 AIモデルのアップグレード**: 本日発表された最新モデル `gemini-3-flash-preview` に更新しました。より高精度な要約・分析が期待できます。

引き続き、コミュニティの知見共有にお役立てください。
"""

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
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
