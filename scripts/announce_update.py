import discord
import asyncio
import os

# Configuration
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
TARGET_CHANNEL_ID = 842348486234341407 # random
IMAGE_PATH = "/Users/kai/.gemini/antigravity/brain/66a3b2d9-635a-4719-8c90-84b7934d6a23/lab_chan_update_banner_1763958519551.png"

ANNOUNCEMENT_TEXT = """
@everyone
**【お知らせ】ラボちゃんのアップデート**

日々の活動を要約するAIエージェント「ラボちゃん」に、以下の機能改善を行いました。

**🆕 アップデート内容:**
1.  **✨ 今日のハイライト**: コミュニティ内の興味深い発言をピックアップして紹介します。
2.  **🔗 リンク機能**: ハイライトされた発言の元メッセージへ、ワンクリックで移動できるようになりました。
3.  **🛡️ 対象チャンネルの拡大**: `random` チャンネルに加え、`tools` や `topics` フォーラムの投稿もレポート対象に追加しました。

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
            with open(IMAGE_PATH, 'rb') as f:
                picture = discord.File(f, filename="update_banner.png")
                await channel.send(content=ANNOUNCEMENT_TEXT, file=picture)
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
