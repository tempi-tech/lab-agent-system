#!/usr/bin/env python3
"""
Discordサーバーから1週間分の投稿ログを取得してmdファイルに出力するスクリプト
全チャンネルのログを構造化して出力
"""

import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
# 取得対象のチャンネルID（環境変数から）
def _parse_id_list(raw: str) -> list[int]:
    return [int(token.strip()) for token in raw.split(",") if token.strip().isdigit()]


SOURCE_CHANNEL_IDS_STR = os.environ.get("SOURCE_CHANNEL_IDS", "")
SOURCE_CHANNEL_IDS = _parse_id_list(SOURCE_CHANNEL_IDS_STR)
SOURCE_CATEGORY_IDS = _parse_id_list(os.environ.get("SOURCE_CATEGORY_IDS", ""))
SOURCE_CHANNEL_EXCLUDE_IDS = set(_parse_id_list(os.environ.get("SOURCE_CHANNEL_EXCLUDE_IDS", "")))

# 出力先
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "logs"
JST = timezone(timedelta(hours=9))


class WeeklyLogFetcher(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        try:
            await self.fetch_and_save_logs()
        finally:
            await self.close()

    async def fetch_messages_from_channel(self, channel, threshold):
        """チャンネル（Text/Forum）からメッセージを取得"""
        messages = []

        if isinstance(channel, discord.TextChannel):
            print(f"  📝 TextChannel: {channel.name}")
            async for msg in channel.history(after=threshold, limit=None):
                messages.append(msg)

        elif isinstance(channel, discord.ForumChannel):
            print(f"  📋 ForumChannel: {channel.name}")
            # アクティブスレッド
            for thread in channel.threads:
                async for msg in thread.history(after=threshold, limit=None):
                    messages.append(msg)
            # アーカイブ済みスレッド
            async for thread in channel.archived_threads(limit=None):
                if thread.archive_timestamp and thread.archive_timestamp > threshold:
                    async for msg in thread.history(after=threshold, limit=None):
                        messages.append(msg)

        return messages

    async def fetch_and_save_logs(self):
        now_utc = datetime.now(timezone.utc)
        threshold = now_utc - timedelta(days=7)

        source_channels = self.resolve_source_channels()

        print(f"\n📅 対象期間: {threshold.astimezone(JST).strftime('%Y-%m-%d %H:%M')} JST 〜 現在")
        print(f"📌 対象チャンネル: {len(source_channels)} 件\n")

        # チャンネルごとにメッセージを整理
        channel_messages = defaultdict(list)
        channel_names = {}

        for channel in source_channels:
            channel_id = channel.id
            channel_names[channel_id] = channel.name
            msgs = await self.fetch_messages_from_channel(channel, threshold)
            channel_messages[channel_id].extend(msgs)
            print(f"     -> {len(msgs)} messages")

        # 日付ごとにグループ化
        daily_messages = defaultdict(lambda: defaultdict(list))
        for channel_id, messages in channel_messages.items():
            for msg in messages:
                date_key = msg.created_at.astimezone(JST).strftime("%Y-%m-%d")
                daily_messages[date_key][channel_id].append(msg)

        # 各日付のメッセージを時間順にソート
        for date_key in daily_messages:
            for channel_id in daily_messages[date_key]:
                daily_messages[date_key][channel_id].sort(key=lambda m: m.created_at)

        total_messages = sum(len(msgs) for msgs in channel_messages.values())
        print(f"\n📊 総メッセージ数: {total_messages}")

        # Markdownを生成
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_file = OUTPUT_DIR / f"weekly_logs_{now_utc.astimezone(JST).strftime('%Y%m%d')}.md"

        lines = self.build_markdown(
            daily_messages,
            channel_names,
            threshold,
            now_utc,
            total_messages,
        )

        output_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n✅ ログを保存しました: {output_file}")

    def build_markdown(self, daily_messages, channel_names, threshold, now_utc, total_messages):
        """構造化されたMarkdownを生成"""
        lines = [
            "# Discord 週間ログ",
            "",
            "## 概要",
            "",
            f"- **取得日時**: {now_utc.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S')} JST",
            f"- **対象期間**: {threshold.astimezone(JST).strftime('%Y-%m-%d %H:%M')} 〜 現在",
            f"- **対象チャンネル数**: {len(channel_names)}",
            f"- **総メッセージ数**: {total_messages}",
            "",
            "## チャンネル一覧",
            "",
        ]

        for channel_id, name in sorted(channel_names.items(), key=lambda x: x[1]):
            lines.append(f"- {name}")

        lines.extend(["", "---", ""])

        # 日付ごとにセクション化
        for date_key in sorted(daily_messages.keys(), reverse=True):
            lines.append(f"## {date_key}")
            lines.append("")

            day_data = daily_messages[date_key]
            for channel_id in sorted(day_data.keys(), key=lambda x: channel_names.get(x, "")):
                channel_name = channel_names.get(channel_id, f"unknown-{channel_id}")
                messages = day_data[channel_id]

                if not messages:
                    continue

                lines.append(f"### #{channel_name}")
                lines.append("")

                for msg in messages:
                    lines.extend(self.format_message(msg))

                lines.append("")

            lines.append("---")
            lines.append("")

        return lines

    def format_message(self, msg):
        """メッセージを整形"""
        lines = []
        timestamp = msg.created_at.astimezone(JST).strftime("%H:%M")

        # 投稿者名
        if isinstance(msg.author, discord.Member):
            author_name = msg.author.display_name
        else:
            author_name = msg.author.name

        # Bot/Adminフラグ
        flags = []
        if msg.author.bot:
            flags.append("Bot")
        if isinstance(msg.author, discord.Member):
            for role in msg.author.roles:
                if "admin" in role.name.lower():
                    flags.append("Admin")
                    break

        flag_str = f" [{', '.join(flags)}]" if flags else ""

        # チャンネルの場所（スレッドの場合は親チャンネルも表示）
        location = ""
        if hasattr(msg.channel, "parent") and msg.channel.parent:
            location = f" (in {msg.channel.parent.name} > {msg.channel.name})"

        lines.append(f"**{timestamp}** | **{author_name}**{flag_str}{location}")

        # メッセージ本文
        if msg.content:
            # 長いメッセージは折りたたみ
            content = msg.content.strip()
            if len(content) > 500:
                lines.append("")
                lines.append("<details>")
                lines.append("<summary>長文メッセージ（クリックで展開）</summary>")
                lines.append("")
                lines.append(content)
                lines.append("")
                lines.append("</details>")
            else:
                lines.append("")
                lines.append(f"> {content.replace(chr(10), chr(10) + '> ')}")

        # 添付ファイル
        if msg.attachments:
            lines.append("")
            for att in msg.attachments:
                lines.append(f"📎 *{att.filename}*")

        # 埋め込み
        if msg.embeds:
            lines.append("")
            lines.append(f"📦 埋め込み: {len(msg.embeds)}件")

        # リアクション
        if msg.reactions:
            reactions_str = " ".join([f"{r.emoji}×{r.count}" for r in msg.reactions])
            lines.append(f"💬 {reactions_str}")

        # 返信先
        if msg.reference and msg.reference.message_id:
            lines.append(f"↩️ 返信先: {msg.reference.message_id}")

        lines.append("")
        return lines

    def resolve_source_channels(self):
        source_ids = set(SOURCE_CHANNEL_IDS)
        for category_id in SOURCE_CATEGORY_IDS:
            category = self.get_channel(category_id)
            if isinstance(category, discord.CategoryChannel):
                for channel in category.channels:
                    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                        source_ids.add(channel.id)
            else:
                print(f"⚠️  Category {category_id} not found or not a category")

        source_ids -= SOURCE_CHANNEL_EXCLUDE_IDS

        channels = []
        for channel_id in sorted(source_ids):
            channel = self.get_channel(channel_id)
            if channel:
                channels.append(channel)
            else:
                print(f"⚠️  Channel {channel_id} not found")
        return channels


async def main():
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not set")
        sys.exit(1)

    if not SOURCE_CHANNEL_IDS and not SOURCE_CATEGORY_IDS:
        print("Error: SOURCE_CHANNEL_IDS and SOURCE_CATEGORY_IDS not set")
        sys.exit(1)

    print("🚀 Discord週間ログ取得を開始します...")
    client = WeeklyLogFetcher()
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
