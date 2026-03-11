"""
Lab Onboarder Agent - Simplified Profile Card System

This agent helps new members create profile cards for the AGI Lab Discord community.
Users input their name, introduction, archetype, and optionally their X (Twitter) link.
If the introduction is left empty, the LLM generates one based on the archetype.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from string import Template
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import discord
from discord import app_commands

from src.agents.lab_onboarder.config import LabOnboarderConfig, load_config
from src.agents.lab_onboarder.storage import ProfileRecord, ProfileStore
from src.core.agent_base import BaseAgent
from src.core.llm import GeminiLLM


# 8 Archetypes for AGI Lab members
ARCHETYPES = {
    "Builder",
    "Researcher",
    "Operator",
    "Curator",
    "Connector",
    "Critic",
    "Creative",
    "Strategist",
}

ARCHETYPE_DESCRIPTIONS = {
    "Builder": "作る人（開発、プロトタイプ）",
    "Researcher": "調べる人（論文、分析）",
    "Operator": "運用する人（自動化、ワークフロー）",
    "Curator": "まとめる人（ニュース、情報整理）",
    "Connector": "繋げる人（コミュニティ、イベント）",
    "Critic": "批評する人（レビュー、議論）",
    "Creative": "創造する人（デザイン、アート）",
    "Strategist": "戦略を立てる人（プロダクト、マーケット）",
}

# Channel recommendation settings
RECOMMEND_CATEGORY_IDS = {1432556670265065492, 1460853093183918266}  # 対象カテゴリ
RECOMMEND_EXCLUDE_IDS = {1459775765251100864, 1457539123182043259, 1460853359358775308}  # 除外

# Fallback channels when LLM fails
FALLBACK_FIXED_CHANNEL_IDS = [
    1463831147392925697,  # ☕ なんでも雑談
    1436182005762097243,  # 🔧 ツールカテゴリの代表チャンネル
    842348486234341407,   # 📰 AIビッグニュース
]
TOOLS_CATEGORY_ID = 1460853093183918266  # Toolsカテゴリ

# Discord snowflake epoch (2015-01-01 00:00:00 UTC in ms)
DISCORD_EPOCH_MS = 1420070400000
ACTIVITY_THRESHOLD_DAYS = 14  # 2週間以内をアクティブとみなす

# Webhook settings for ラボちゃん persona
LABCHAN_WEBHOOK_NAME = "LabOnboarder Webhook"
LABCHAN_DISPLAY_NAME = "ラボちゃん（研究生）"
# Use the same avatar as daily_reporter
LABCHAN_AVATAR_PATH = os.path.join(
    os.path.dirname(__file__), "..", "daily_reporter", "assets", "lab-chan.jpeg"
)

# LLM prompt for channel recommendations
CHANNEL_RECOMMENDATION_PROMPT = """
あなたは「ラボちゃん」です。AGIラボDiscordの元気な案内役として、新メンバーにチャンネルを推薦します。

## キャラクター設定
- **性格**: 元気で親しみやすい、褒め上手
- **語尾**: 「〜ですね！」「〜かも！」「〜ッス！」（元気な敬語）
- **役割**: 新メンバーの興味に合わせてチャンネルを紹介する

# メンバー情報
表示名: $display_name
タグ: $tags
自己紹介: $introduction

# 利用可能なチャンネル・スレッド一覧
🟢 = 2週間以内にアクティブ、⚪ = 非アクティブ
$channel_list

# マッチングルール（重要度順）
1. **ツール名マッチ**: 自己紹介に「Claude Code」「ChatGPT」「Cursor」等のツール名があれば、該当スレッドを最優先
2. **興味マッチ**: 趣味・興味（開発、研究、動画生成、映画、漫画等）があれば関連スレッドを推薦
3. **アクティビティ優先**: 基本的に🟢（アクティブ）を優先。ただしツール名が完全一致する場合は⚪でもOK

# 推薦すべきもの
- ツール別スレッド（Claude Code, Cursor, ChatGPT等）- 自己紹介にツール名があれば必須
- トピック別スレッド（開発、研究×AI、動画生成等）
- inputしたよ、AIビッグニュース（情報好きな人）
- 雑談系（映画、漫画、ライフスタイル等の趣味があれば）

# 推薦しないもの
- welcome、自己紹介系（既にいる場所）
- アナウンス、運営系（ユーザーが投稿する場所ではない）
- ラボちゃんの部屋（見るだけ）

# 出力形式（重要）
以下のJSON形式で出力してください：
{"summary": "ラボちゃんの口調で、ユーザーの興味に共感しながらチャンネルを紹介する一言（60文字以内）", "channels": [チャンネルIDの配列、8〜10個]}

## summaryのルール
- ラボちゃんの元気な口調で書く（「〜ですね！」「〜かも！」）
- ユーザーの興味に共感する（「○○に興味あるんですね！」「○○使ってるんですね！」）
- おすすめ感を出す（「ピッタリのチャンネルがありますよ！」「きっと楽しめるかも！」）
- 「〜さんは」で始めない
- 60文字以内

## 出力例
{"summary":"AIツール好きなんですね！Claude CodeやChatGPTの話で盛り上がれるチャンネルがありますよ！","channels":[123456789012345678,234567890123456789]}
{"summary":"シンギュラリティに興味あるんですね！SF好きな仲間もいるので楽しめるかも！","channels":[123456789012345678,234567890123456789]}
""".strip()

# LLM prompt for generating introduction when user leaves it empty
INTRO_GENERATION_PROMPT = """
あなたはAGIラボDiscordの新メンバーの自己紹介を作成するアシスタントです。

以下の情報をもとに、コミュニティへの短い自己紹介文を一人称で作成してください。
- 2〜3文程度で簡潔に
- タイプに合った興味や活動を含める
- 「よろしくお願いします」「気軽に話しかけてください」などのフレンドリーな締めくくり
- 100〜200文字程度
- バリエーションを持たせて（毎回同じにならないように）

表示名: {display_name}
タイプ: {archetype} ({archetype_description})
Xリンク: {x_info}

出力は紹介文のテキストのみ（余計な説明なし）。
""".strip()


@dataclass
class ProfileCard:
    """Simplified profile card for display."""

    display_name: str
    introduction: str
    archetype: str
    x_profile_url: Optional[str]


class LabOnboarderAgent(BaseAgent):
    def __init__(self) -> None:
        self.config: LabOnboarderConfig = load_config()
        self.store = ProfileStore(self.config.sqlite_path)
        self._client: Optional[discord.Client] = None
        self._commands_registered = False
        self._debug = self.config.debug
        self._log_path = self.config.log_path
        self._log_queue: Optional[asyncio.Queue[str]] = None
        self._log_sender_task: Optional[asyncio.Task[None]] = None
        self._processed_threads: set[int] = set()  # Prevent double processing

    @property
    def name(self) -> str:
        return "lab_onboarder"

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        if not hasattr(client, "tree"):
            client.tree = app_commands.CommandTree(client)

        if self.config.log_channel_id and self._log_sender_task is None:
            self._log_queue = asyncio.Queue(maxsize=500)
            self._log_sender_task = asyncio.create_task(self._log_sender_loop())

        if not self._commands_registered:
            guild = discord.Object(id=self.config.guild_id)

            @client.tree.command(
                name="onboard",
                description="プロフィールカードを作成/更新します",
                guild=guild,
            )
            async def onboard_cmd(interaction: discord.Interaction) -> None:
                await interaction.response.send_modal(OnboardModal(self, interaction.user))

            @client.tree.command(
                name="profile_edit",
                description="プロフィールカードを編集します",
                guild=guild,
            )
            async def profile_edit_cmd(interaction: discord.Interaction) -> None:
                if not await self._check_authorized(interaction):
                    await self._send_interaction_message(
                        interaction,
                        "このコマンドを使う権限がありません。",
                        ephemeral=True,
                    )
                    return
                existing = self.store.get_profile(interaction.user.id)
                if not existing:
                    await self._send_interaction_message(
                        interaction,
                        "プロフィールがまだありません。先に `/onboard` で作成してください。",
                        ephemeral=True,
                    )
                    return
                await interaction.response.send_modal(ProfileEditModal(self, interaction.user))

            @client.tree.command(
                name="lab_debug_dm",
                description="（管理者用）オンボーディングDMを送信します",
                guild=guild,
            )
            @app_commands.describe(user="送信先ユーザー（未指定なら自分）")
            async def debug_dm_cmd(
                interaction: discord.Interaction,
                user: Optional[discord.Member] = None,
            ) -> None:
                await self._handle_debug_dm(interaction, user)

            @client.tree.command(
                name="lab_debug_reset",
                description="（管理者用）プロフィール情報をリセット",
                guild=guild,
            )
            @app_commands.describe(
                user="対象ユーザー（未指定なら自分）",
                delete_thread="プロフィールスレッドも削除する",
            )
            async def debug_reset_cmd(
                interaction: discord.Interaction,
                user: Optional[discord.Member] = None,
                delete_thread: bool = False,
            ) -> None:
                await self._handle_debug_reset(interaction, user, delete_thread)

            @client.tree.command(
                name="lab_debug_show",
                description="（管理者用）プロフィール情報を表示",
                guild=guild,
            )
            @app_commands.describe(user="対象ユーザー（未指定なら自分）")
            async def debug_show_cmd(
                interaction: discord.Interaction,
                user: Optional[discord.Member] = None,
            ) -> None:
                await self._handle_debug_show(interaction, user)

            @client.tree.command(
                name="lab_debug_logs",
                description="（管理者用）最近のログを表示",
                guild=guild,
            )
            @app_commands.describe(lines="表示する行数（10〜200）")
            async def debug_logs_cmd(
                interaction: discord.Interaction,
                lines: Optional[int] = None,
            ) -> None:
                await self._handle_debug_logs(interaction, lines)

            @client.tree.command(
                name="lab_test_recommend",
                description="（管理者用）チャンネル推薦をテスト",
                guild=guild,
            )
            @app_commands.describe(
                user="対象ユーザー（未指定なら自分）",
                archetype="テスト用アーキタイプ（上書き）",
                introduction="テスト用自己紹介（上書き）",
                dry_run="Trueなら投稿せずプレビューのみ",
                show_prompt="Trueならプロンプト全文を表示",
            )
            async def test_recommend_cmd(
                interaction: discord.Interaction,
                user: Optional[discord.Member] = None,
                archetype: Optional[str] = None,
                introduction: Optional[str] = None,
                dry_run: bool = True,
                show_prompt: bool = False,
            ) -> None:
                await self._handle_test_recommend(
                    interaction, user, archetype, introduction, dry_run, show_prompt
                )

            try:
                await client.tree.sync(guild=guild)
                print("[LabOnboarder] Slash commands synced.")
            except Exception as exc:
                print(f"[LabOnboarder] Slash command sync failed: {exc}")

            client.add_view(OnboardView(self))
            self._commands_registered = True

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.config.guild_id:
            return
        allow_ids = self.config.dm_allowlist_user_ids
        allow_names = self.config.dm_allowlist_usernames
        if allow_ids is not None or allow_names is not None:
            allowed = False
            if allow_ids and member.id in allow_ids:
                allowed = True
            if not allowed and allow_names:
                name = (member.name or "").lower()
                display = (member.display_name or "").lower()
                if name in allow_names or display in allow_names:
                    allowed = True
            if not allowed:
                return
        try:
            await self._send_welcome_dm(member)
        except discord.Forbidden:
            return
        except Exception as exc:
            print(f"[LabOnboarder] Failed to send welcome DM: {exc}")

    async def _handle_onboard(
        self,
        interaction: discord.Interaction,
        display_name: str,
        introduction: str,
        archetype: str,
        x_profile_url: Optional[str],
    ) -> None:
        self._log(
            f"onboard request user={interaction.user.id} "
            f"display_name={display_name[:20]} archetype={archetype}"
        )
        await self._defer(interaction)

        if not await self._check_authorized(interaction):
            self._log(f"onboard unauthorized user={interaction.user.id}")
            await self._send_followup(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        # Validate archetype
        normalized_archetype = _normalize_archetype(archetype)
        if normalized_archetype is None:
            await self._send_followup(
                interaction,
                f"⚠️ タイプが正しくありません。以下から選んでください:\n"
                f"{', '.join(sorted(ARCHETYPES))}",
                ephemeral=True,
            )
            return

        # Validate X URL if provided
        normalized_x = _normalize_x_url(x_profile_url) if x_profile_url else None
        if x_profile_url and x_profile_url.strip() and normalized_x is None:
            await self._send_followup(
                interaction,
                "⚠️ X URLの形式が正しくありません。`https://x.com/username` のように入力してください。",
                ephemeral=True,
            )
            return

        try:
            # Build profile (generate introduction if empty)
            card = await self._build_profile(
                display_name=display_name.strip(),
                introduction=introduction.strip(),
                archetype=normalized_archetype,
                x_profile_url=normalized_x,
            )

            # Create/update forum thread
            thread_id, message_id = await self._upsert_forum_thread(
                interaction.user,
                card,
            )

            # Save to database
            self.store.upsert_profile(
                discord_user_id=interaction.user.id,
                display_name=card.display_name,
                introduction=card.introduction,
                archetype=card.archetype,
                x_profile_url=card.x_profile_url,
                forum_thread_id=thread_id,
                forum_message_id=message_id,
            )

            # Post channel recommendations
            await self._post_channel_recommendations(
                interaction.user,
                thread_id,
                card,
            )

            await self._send_followup(
                interaction,
                "✅ プロフィールカードを作成/更新しました！\n"
                "#profiles のスレッドを確認してください。\n"
                "💡 `/profile_edit` で編集できます。",
                ephemeral=True,
            )

        except Exception as exc:
            self._log(f"onboard error user={interaction.user.id} err={exc}")
            await self._send_followup(
                interaction,
                f"⚠️ エラー: {exc}",
                ephemeral=True,
            )

    async def _build_profile(
        self,
        display_name: str,
        introduction: str,
        archetype: str,
        x_profile_url: Optional[str],
    ) -> ProfileCard:
        """Build a profile card, generating introduction if empty."""
        # If introduction is provided, use it directly
        if introduction:
            return ProfileCard(
                display_name=display_name,
                introduction=introduction,
                archetype=archetype,
                x_profile_url=x_profile_url,
            )

        # Generate introduction with LLM
        generated = await self._generate_introduction(
            display_name, archetype, x_profile_url
        )
        return ProfileCard(
            display_name=display_name,
            introduction=generated,
            archetype=archetype,
            x_profile_url=x_profile_url,
        )

    async def _generate_introduction(
        self,
        display_name: str,
        archetype: str,
        x_profile_url: Optional[str],
    ) -> str:
        """Generate an introduction using LLM when user leaves it empty."""
        x_info = x_profile_url if x_profile_url else "なし"
        archetype_desc = ARCHETYPE_DESCRIPTIONS.get(archetype, archetype)

        prompt = INTRO_GENERATION_PROMPT.format(
            display_name=display_name,
            archetype=archetype,
            archetype_description=archetype_desc,
            x_info=x_info,
        )

        try:
            llm = GeminiLLM(model=self.config.llm_model)
            result = await llm.generate(prompt)
            if result and result.text and result.text.strip():
                return result.text.strip()
        except Exception as exc:
            self._log(f"intro generation failed: {exc}")

        # Fallback if LLM fails
        return f"AGIラボに参加しました。{archetype_desc}タイプです。よろしくお願いします！"

    async def _upsert_forum_thread(
        self,
        user: discord.User | discord.Member,
        card: ProfileCard,
    ) -> tuple[int, int]:
        forum = await self._get_forum_channel()
        if forum is None:
            raise RuntimeError("PROFILE_FORUM_CHANNEL_ID is not a Forum channel.")

        existing = self.store.get_profile(user.id)
        thread_name = f"{card.display_name} | {card.archetype}"
        content = f"<@{user.id}>"
        embed = self._make_profile_embed(card, user)

        if existing and existing.forum_thread_id:
            thread = await self._fetch_thread(existing.forum_thread_id)
            if thread:
                message_id = existing.forum_message_id or thread.id
                try:
                    message = await thread.fetch_message(message_id)
                    await message.edit(content=content, embed=embed)
                    try:
                        await thread.edit(name=thread_name)
                    except Exception:
                        pass
                    return thread.id, message.id
                except Exception:
                    message = await self._fetch_thread_starter_message(thread)
                    if message:
                        await message.edit(content=content, embed=embed)
                        return thread.id, message.id
                    msg = await thread.send(content=content, embed=embed)
                    return thread.id, msg.id

        created = await forum.create_thread(
            name=thread_name,
            content=content,
            embed=embed,
        )

        thread = getattr(created, "thread", created)
        message = getattr(created, "message", None)
        message_id = getattr(message, "id", None) or thread.id
        return thread.id, message_id

    def _make_profile_embed(
        self,
        card: ProfileCard,
        user: discord.User | discord.Member,
    ) -> discord.Embed:
        """Create a simplified profile embed."""
        title = f"{card.display_name} | {card.archetype}"

        mention = f"<@{user.id}>"
        description = f"{mention}\n\n{card.introduction}"

        embed = discord.Embed(
            title=title,
            description=description,
        )

        if card.x_profile_url:
            handle = _extract_x_handle(card.x_profile_url)
            x_label = f"@{handle}" if handle else "Xプロフィール"
            embed.add_field(name="X", value=f"[{x_label}]({card.x_profile_url})", inline=False)

        try:
            avatar_url = user.display_avatar.url
        except Exception:
            avatar_url = None
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text="Generated by ラボちゃん先輩")
        return embed

    async def _post_channel_recommendations(
        self,
        user: discord.User | discord.Member,
        thread_id: Optional[int],
        card: ProfileCard,
    ) -> None:
        self._log(f"[DEBUG] _post_channel_recommendations start thread_id={thread_id}")

        if thread_id is None:
            self._log("[DEBUG] skipped: thread_id is None")
            return
        thread = await self._fetch_thread(thread_id)
        if thread is None:
            self._log("[DEBUG] skipped: thread not found")
            return

        guild = self._client.get_guild(self.config.guild_id) if self._client else None
        if not guild:
            self._log("recommendations skipped: guild not found")
            return

        # Get user's tags from their profile thread
        tags = []
        if hasattr(thread, "applied_tags") and thread.applied_tags:
            tags = [tag.name for tag in thread.applied_tags]
        self._log(f"[DEBUG] tags from thread={tags}")

        # Build channel and thread list
        channel_list = await self._build_channel_thread_list(guild)
        if not channel_list:
            self._log("recommendations skipped: no channels found")
            return
        self._log(f"[DEBUG] channel_list count={len(channel_list)}")

        # Build prompt
        prompt = Template(CHANNEL_RECOMMENDATION_PROMPT).safe_substitute(
            display_name=card.display_name,
            tags=", ".join(tags) if tags else "なし",
            introduction=card.introduction,
            channel_list="\n".join(channel_list),
        )
        self._log(f"[DEBUG] prompt length={len(prompt)} chars")

        summary: Optional[str] = None
        is_fallback = False

        try:
            llm = GeminiLLM(model=self.config.llm_model)
            self._log(f"[DEBUG] calling LLM model={self.config.llm_model}")
            result = await llm.generate(prompt, max_output_tokens=2048)
            finish_reason = result.raw.get("finish_reason") if isinstance(result.raw, dict) else None
            self._log(f"[DEBUG] LLM raw response length={len(result.text)} chars, finish_reason={finish_reason}")
            self._log(f"[DEBUG] LLM raw response: {result.text[:500]}")
            summary, recommended_ids = _parse_recommendation_response(result.text)
            self._log(f"[DEBUG] parsed summary={summary[:50] if summary else None} IDs count={len(recommended_ids)}")
        except Exception as exc:
            self._log(f"channel recommendation LLM failed: {exc}, using fallback")
            recommended_ids = await self._get_fallback_channels(guild)
            is_fallback = True

        if not recommended_ids:
            self._log("LLM returned empty list, using fallback")
            recommended_ids = await self._get_fallback_channels(guild)
            is_fallback = True

        # Filter out invalid channel IDs (ones that don't exist in guild)
        valid_ids: List[int] = []
        invalid_ids: List[int] = []
        for rid in recommended_ids[:10]:
            ch = guild.get_channel(rid) or guild.get_thread(rid)
            if ch:
                valid_ids.append(rid)
            else:
                invalid_ids.append(rid)
        self._log(f"[DEBUG] valid_ids={len(valid_ids)} invalid_ids={len(invalid_ids)} invalid={invalid_ids}")

        if not valid_ids and not is_fallback:
            self._log("no valid channel IDs from LLM, using fallback")
            recommended_ids = await self._get_fallback_channels(guild)
            is_fallback = True
            valid_ids = []
            invalid_ids = []
            for rid in recommended_ids[:10]:
                ch = guild.get_channel(rid) or guild.get_thread(rid)
                if ch:
                    valid_ids.append(rid)
                else:
                    invalid_ids.append(rid)
            self._log(
                f"[DEBUG] fallback valid_ids={len(valid_ids)} invalid_ids={len(invalid_ids)} invalid={invalid_ids}"
            )

        if not valid_ids:
            self._log("recommendations skipped: no valid channel IDs even with fallback")
            return

        # Post recommendations with summary if available
        lines = [
            f"💡 **{user.mention}さんへのおすすめチャンネル**",
            "自己紹介から、あなたに合いそうなチャンネルをピックアップしました！",
            "",
        ]

        # Add summary if available (not for fallback)
        if summary and not is_fallback:
            lines.append(f"_{summary}_")
            lines.append("")

        lines.append("📌 **おすすめ**")
        for idx, rid in enumerate(valid_ids, start=1):
            lines.append(f"{idx}. <#{rid}>")

        self._log(f"[DEBUG] posting {len(valid_ids)} recommendations to thread={thread_id}")
        allowed_mentions = discord.AllowedMentions(users=[user], roles=False, everyone=False)

        # Try to post via webhook (ラボちゃん persona)
        posted = False
        if thread.parent and isinstance(thread.parent, discord.ForumChannel):
            webhook = await self._get_or_create_webhook(thread.parent)
            if webhook:
                try:
                    await webhook.send(
                        "\n".join(lines),
                        username=LABCHAN_DISPLAY_NAME,
                        thread=thread,
                        allowed_mentions=allowed_mentions,
                    )
                    self._log(f"[DEBUG] webhook post success thread={thread_id}")
                    posted = True
                except Exception as exc:
                    self._log(f"webhook post failed: {exc}, falling back to direct send")

        # Fallback to direct send if webhook fails
        if not posted:
            try:
                await thread.send("\n".join(lines), allowed_mentions=allowed_mentions)
                self._log(f"[DEBUG] direct post success thread={thread_id}")
            except Exception as exc:
                self._log(f"recommendations post failed user={user.id} err={exc}")

    def _is_recently_active(self, snowflake_id: int | None) -> bool:
        """Check if a snowflake ID (message/thread) is from the last N days."""
        if not snowflake_id:
            return False
        timestamp_ms = (snowflake_id >> 22) + DISCORD_EPOCH_MS
        last_active = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        threshold = datetime.now(tz=timezone.utc) - timedelta(days=ACTIVITY_THRESHOLD_DAYS)
        return last_active > threshold

    async def _get_fallback_channels(self, guild: discord.Guild) -> List[int]:
        """Get fallback channel IDs when LLM fails."""
        fallback_ids = list(FALLBACK_FIXED_CHANNEL_IDS)

        # Get active channels from Tools category (top 3)
        tools_channels: List[tuple[int, int]] = []
        for ch in guild.channels:
            if ch.category and ch.category.id == TOOLS_CATEGORY_ID:
                if isinstance(ch, discord.TextChannel):
                    last_msg_id = ch.last_message_id
                    if last_msg_id and self._is_recently_active(last_msg_id):
                        tools_channels.append((ch.id, last_msg_id))
                elif isinstance(ch, discord.ForumChannel):
                    # For forum, check active threads
                    for t in ch.threads:
                        last_msg_id = t.last_message_id
                        if last_msg_id and self._is_recently_active(last_msg_id):
                            tools_channels.append((t.id, last_msg_id))
                            break  # One active thread per forum is enough

        # Sort by last_message_id (newer first) and take top 3
        tools_channels.sort(key=lambda x: x[1], reverse=True)
        for ch_id, _ in tools_channels[:3]:
            if ch_id not in fallback_ids:
                fallback_ids.append(ch_id)

        return fallback_ids

    async def _get_or_create_webhook(
        self, channel: discord.ForumChannel
    ) -> Optional[discord.Webhook]:
        """Get or create a webhook for ラボちゃん persona."""
        try:
            webhooks = await channel.webhooks()
            webhook = None
            for wh in webhooks:
                if wh.name == LABCHAN_WEBHOOK_NAME:
                    webhook = wh
                    break

            if not webhook:
                webhook = await channel.create_webhook(name=LABCHAN_WEBHOOK_NAME)
                self._log(f"[DEBUG] created webhook for forum channel={channel.id}")

            # Update avatar if file exists
            if os.path.exists(LABCHAN_AVATAR_PATH):
                try:
                    with open(LABCHAN_AVATAR_PATH, "rb") as f:
                        avatar_bytes = f.read()
                    await webhook.edit(avatar=avatar_bytes)
                    self._log("[DEBUG] webhook avatar updated")
                except Exception as e:
                    self._log(f"[DEBUG] webhook avatar update failed: {e}")

            return webhook
        except Exception as exc:
            self._log(f"webhook creation failed: {exc}")
            return None

    async def _build_channel_thread_list(
        self, guild: discord.Guild, max_items: int = 100
    ) -> List[str]:
        """Build a list of channels and threads for LLM recommendation."""
        items = []
        active_threads = []
        inactive_threads = []

        for ch in guild.channels:
            # Skip if not in allowed categories or in exclude list
            category_id = ch.category.id if ch.category else None
            if category_id not in RECOMMEND_CATEGORY_IDS:
                continue
            if ch.id in RECOMMEND_EXCLUDE_IDS:
                continue

            if isinstance(ch, discord.TextChannel):
                category = ch.category.name if ch.category else "なし"
                is_active = self._is_recently_active(ch.last_message_id)
                status = "🟢" if is_active else "⚪"
                items.append(f"ID:{ch.id} {status} 名前:{ch.name} カテゴリ:{category}")

            elif isinstance(ch, discord.ForumChannel):
                # Get threads: cached active + archived
                try:
                    seen_ids = set()
                    # Active threads (cached)
                    for t in ch.threads:
                        if t.id not in seen_ids:
                            seen_ids.add(t.id)
                            is_active = self._is_recently_active(t.last_message_id)
                            entry = f"ID:{t.id} 名前:{t.name} 親:{ch.name}"
                            if is_active:
                                active_threads.append(f"{entry} 🟢")
                            else:
                                inactive_threads.append(f"{entry} ⚪")

                    # Archived threads (fetch up to 20 per forum)
                    async for t in ch.archived_threads(limit=20):
                        if t.id not in seen_ids:
                            seen_ids.add(t.id)
                            is_active = self._is_recently_active(t.last_message_id)
                            entry = f"ID:{t.id} 名前:{t.name} 親:{ch.name}"
                            if is_active:
                                active_threads.append(f"{entry} 🟢")
                            else:
                                inactive_threads.append(f"{entry} ⚪")
                except Exception as e:
                    self._log(f"thread fetch error for {ch.name}: {e}")

        # Order: active threads > inactive threads > channels
        result = active_threads + inactive_threads[:20] + items
        return result[:max_items]

    async def _find_user_profile_in_forum(
        self, user_id: int
    ) -> Optional[tuple[discord.Thread, str, List[str]]]:
        """
        Find a user's profile thread in the profile forum.
        Returns (thread, introduction_content, tags) or None if not found.
        """
        if not self._client:
            return None

        guild = self._client.get_guild(self.config.guild_id)
        if not guild:
            return None

        forum = guild.get_channel(self.config.profile_forum_channel_id)
        if not isinstance(forum, discord.ForumChannel):
            return None

        # Search in active threads
        for thread in forum.threads:
            if thread.owner_id == user_id:
                try:
                    starter = thread.starter_message or await thread.fetch_message(thread.id)
                    if starter and starter.content:
                        tags = [tag.name for tag in thread.applied_tags] if thread.applied_tags else []
                        return (thread, starter.content, tags)
                except Exception:
                    pass

        # Search in archived threads
        try:
            async for thread in forum.archived_threads(limit=50):
                if thread.owner_id == user_id:
                    try:
                        starter = await thread.fetch_message(thread.id)
                        if starter and starter.content:
                            tags = [tag.name for tag in thread.applied_tags] if thread.applied_tags else []
                            return (thread, starter.content, tags)
                    except Exception:
                        pass
        except Exception:
            pass

        return None

    async def on_thread_create(self, thread: discord.Thread) -> None:
        """Handle thread creation in the profile forum."""
        self._log(f"[DEBUG] on_thread_create called thread={thread.id} parent={thread.parent_id}")

        # Only handle threads in the profile forum
        if thread.parent_id != self.config.profile_forum_channel_id:
            self._log(f"[DEBUG] skipped: parent_id mismatch (expected={self.config.profile_forum_channel_id})")
            return

        # Ignore threads created by the bot itself (already processed via /onboard)
        if self._client and thread.owner_id == self._client.user.id:
            self._log("[DEBUG] skipped: bot's own thread")
            return

        # Prevent double processing (Discord may fire event multiple times)
        if thread.id in self._processed_threads:
            self._log(f"[DEBUG] skipped: already processed thread={thread.id}")
            return
        self._processed_threads.add(thread.id)
        self._log(f"[DEBUG] added to processed set, size={len(self._processed_threads)}")

        self._log(f"thread_create detected thread={thread.id} owner={thread.owner_id}")

        # Get the starter message
        try:
            starter = thread.starter_message or await thread.fetch_message(thread.id)
            self._log(f"[DEBUG] starter message fetched, has_content={bool(starter and starter.content)}")
        except Exception as exc:
            self._log(f"thread_create fetch starter failed: {exc}")
            return

        if not starter or not starter.content:
            self._log("thread_create skipped: no starter content")
            return

        # Get tags from thread
        tags = []
        if hasattr(thread, "applied_tags") and thread.applied_tags:
            tags = [tag.name for tag in thread.applied_tags]
        self._log(f"[DEBUG] thread tags={tags}")

        # Build a simple profile card for recommendations
        card = ProfileCard(
            display_name=thread.owner.display_name if thread.owner else thread.name,
            introduction=starter.content,
            archetype="Curator",  # Default archetype
            x_profile_url=None,
        )
        self._log(f"[DEBUG] calling _post_channel_recommendations for thread={thread.id}")

        await self._post_channel_recommendations(
            thread.owner or starter.author,
            thread.id,
            card,
        )
        self._log(f"[DEBUG] _post_channel_recommendations completed for thread={thread.id}")

    async def _handle_debug_dm(
        self,
        interaction: discord.Interaction,
        target: Optional[discord.Member],
    ) -> None:
        if not await self._check_admin(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        member = target or interaction.user
        if not isinstance(member, (discord.Member, discord.User)):
            await self._send_interaction_message(
                interaction,
                "対象ユーザーが見つかりません。",
                ephemeral=True,
            )
            return

        try:
            self._log(f"debug_dm send to user={member.id}")
            await self._send_welcome_dm(member)
            await self._send_interaction_message(
                interaction,
                f"✅ DMを送信しました: {member}",
                ephemeral=True,
            )
        except discord.Forbidden:
            await self._send_interaction_message(
                interaction,
                "⚠️ DMを送信できませんでした（相手がDM拒否の可能性）。",
                ephemeral=True,
            )
        except Exception as exc:
            await self._send_interaction_message(
                interaction,
                f"⚠️ DM送信に失敗: {exc}",
                ephemeral=True,
            )

    async def _handle_debug_reset(
        self,
        interaction: discord.Interaction,
        target: Optional[discord.Member],
        delete_thread: bool,
    ) -> None:
        if not await self._check_admin(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        member = target or interaction.user
        if not isinstance(member, (discord.Member, discord.User)):
            await self._send_interaction_message(
                interaction,
                "対象ユーザーが見つかりません。",
                ephemeral=True,
            )
            return

        record = self.store.get_profile(member.id)
        if record is None:
            await self._send_interaction_message(
                interaction,
                "⚠️ 対象ユーザーのプロフィールが見つかりません。",
                ephemeral=True,
            )
            return

        if delete_thread and record.forum_thread_id:
            thread = await self._fetch_thread(record.forum_thread_id)
            if thread:
                try:
                    await thread.delete()
                except Exception as exc:
                    await self._send_interaction_message(
                        interaction,
                        f"⚠️ スレッド削除に失敗: {exc}",
                        ephemeral=True,
                    )
                    return

        self.store.delete_profile(member.id)
        await self._send_interaction_message(
            interaction,
            f"✅ {member} のプロフィール情報をリセットしました。",
            ephemeral=True,
        )

    async def _handle_debug_show(
        self,
        interaction: discord.Interaction,
        target: Optional[discord.Member],
    ) -> None:
        if not await self._check_admin(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        member = target or interaction.user
        if not isinstance(member, (discord.Member, discord.User)):
            await self._send_interaction_message(
                interaction,
                "対象ユーザーが見つかりません。",
                ephemeral=True,
            )
            return

        record = self.store.get_profile(member.id)
        if record is None:
            await self._send_interaction_message(
                interaction,
                "⚠️ 対象ユーザーのプロフィールが見つかりません。",
                ephemeral=True,
            )
            return

        lines = [
            f"**{member}** のプロフィール",
            f"- display_name: {record.display_name}",
            f"- archetype: {record.archetype}",
            f"- x_profile_url: {record.x_profile_url or 'なし'}",
            f"- forum_thread_id: {record.forum_thread_id}",
            f"- created_at: {record.created_at}",
            f"- updated_at: {record.updated_at}",
            "",
            "**introduction:**",
            record.introduction[:500] if record.introduction else "(なし)",
        ]
        await self._send_interaction_message(
            interaction,
            "\n".join(lines),
            ephemeral=True,
        )

    async def _handle_debug_logs(
        self,
        interaction: discord.Interaction,
        lines: Optional[int],
    ) -> None:
        if not await self._check_admin(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        count = max(10, min(200, lines or 50))
        content = self._tail_log_lines(count)
        if not content:
            await self._send_interaction_message(
                interaction,
                "ログがありません。",
                ephemeral=True,
            )
            return

        if len(content) > 1900:
            content = content[-1900:]
        await self._send_interaction_message(
            interaction,
            f"```\n{content}\n```",
            ephemeral=True,
        )

    async def _handle_test_recommend(
        self,
        interaction: discord.Interaction,
        target: Optional[discord.Member],
        archetype_override: Optional[str],
        introduction_override: Optional[str],
        dry_run: bool,
        show_prompt: bool,
    ) -> None:
        """Test channel recommendations with various options."""
        if not await self._check_admin(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        await self._defer(interaction)

        member = target or interaction.user
        if not isinstance(member, (discord.Member, discord.User)):
            await self._send_followup(
                interaction,
                "対象ユーザーが見つかりません。",
                ephemeral=True,
            )
            return

        # Get profile: DB first, then forum fallback
        record = self.store.get_profile(member.id)
        tags = []

        if record and record.introduction:
            # Found in DB
            display_name = record.display_name
            archetype = archetype_override or record.archetype or "Curator"
            introduction = introduction_override or record.introduction
            thread_id = record.forum_thread_id
            # Get tags from thread if available
            if thread_id:
                user_thread = await self._fetch_thread(thread_id)
                if user_thread and hasattr(user_thread, "applied_tags") and user_thread.applied_tags:
                    tags = [tag.name for tag in user_thread.applied_tags]
        else:
            # Not in DB, try to find in forum
            forum_result = await self._find_user_profile_in_forum(member.id)
            if forum_result:
                thread, forum_intro, forum_tags = forum_result
                display_name = member.display_name
                archetype = archetype_override or "Curator"
                introduction = introduction_override or forum_intro
                thread_id = thread.id
                tags = forum_tags
                self._log(f"test_recommend found forum profile for user={member.id}")
            else:
                # Not found anywhere
                display_name = member.display_name
                archetype = archetype_override or "Curator"
                introduction = introduction_override or "(プロフィール未作成)"
                thread_id = None

        # Validate archetype if overridden
        if archetype_override:
            normalized = _normalize_archetype(archetype_override)
            if normalized:
                archetype = normalized
            else:
                await self._send_followup(
                    interaction,
                    f"⚠️ 無効なarchetype: {archetype_override}\n有効: {', '.join(sorted(ARCHETYPES))}",
                    ephemeral=True,
                )
                return

        # Build channel list
        guild = self._client.get_guild(self.config.guild_id) if self._client else None
        if not guild:
            await self._send_followup(
                interaction, "⚠️ ギルドが見つかりません。", ephemeral=True
            )
            return

        # Build channel and thread list (tags already fetched above)
        channel_list = await self._build_channel_thread_list(guild)
        if not channel_list:
            await self._send_followup(
                interaction, "⚠️ チャンネルが見つかりません。", ephemeral=True
            )
            return

        # Build prompt
        prompt = Template(CHANNEL_RECOMMENDATION_PROMPT).safe_substitute(
            display_name=display_name,
            tags=", ".join(tags) if tags else "なし",
            introduction=introduction,
            channel_list="\n".join(channel_list),
        )

        # Show prompt if requested (ephemeral only)
        if show_prompt:
            # Split long prompt for Discord
            prompt_preview = prompt[:1800] + "..." if len(prompt) > 1800 else prompt
            await self._send_followup(
                interaction,
                f"**📝 プロンプト（{len(channel_list)}チャンネル）:**\n```\n{prompt_preview}\n```",
                ephemeral=True,
            )

        # Call LLM
        try:
            llm = GeminiLLM(model=self.config.llm_model)
            result = await llm.generate(prompt, max_output_tokens=2048)
            raw_response = result.text.strip()
            summary, recommended_ids = _parse_recommendation_response(raw_response)
        except Exception as exc:
            await self._send_followup(
                interaction,
                f"⚠️ LLM呼び出しに失敗: {exc}",
                ephemeral=True,
            )
            return

        if not recommended_ids:
            await self._send_followup(
                interaction,
                "⚠️ LLMが空の推薦を返しました。",
                ephemeral=True,
            )
            return

        # Build result message
        lines = [
            f"**🧪 チャンネル推薦テスト結果**",
            f"対象: {member.mention} ({display_name})",
            f"タグ: {', '.join(tags) if tags else 'なし'}",
            f"自己紹介: {introduction[:100]}{'...' if len(introduction) > 100 else ''}",
            "",
        ]

        # Show summary if available
        if summary:
            lines.append(f"**💭 概要:** _{summary}_")
            lines.append("")

        lines.append(f"**📌 推薦（{len(recommended_ids)}件）:**")
        for idx, rid in enumerate(recommended_ids[:10], start=1):
            # Check if channel or thread exists
            ch = guild.get_channel(rid) or guild.get_thread(rid)
            if ch:
                lines.append(f"{idx}. <#{rid}> ✓")
            else:
                lines.append(f"{idx}. ID:{rid} ⚠️ (存在しない)")

        lines.append("")
        lines.append(f"**LLM生レスポンス:** \n```json\n{raw_response[:500]}\n```")

        if dry_run:
            lines.append("")
            lines.append("📋 **ドライラン**: 投稿されていません")
            await self._send_followup(
                interaction,
                "\n".join(lines),
                ephemeral=True,
            )
        else:
            # Actually post to the thread
            if not thread_id:
                lines.append("")
                lines.append("⚠️ プロフィールスレッドがないため投稿できません")
                await self._send_followup(
                    interaction,
                    "\n".join(lines),
                    ephemeral=True,
                )
                return

            thread = await self._fetch_thread(thread_id)
            if not thread:
                lines.append("")
                lines.append(f"⚠️ スレッド(ID:{thread_id})が見つかりません")
                await self._send_followup(
                    interaction,
                    "\n".join(lines),
                    ephemeral=True,
                )
                return

            # Build post content (same format as _post_channel_recommendations)
            post_lines = [
                f"💡 **{member.mention}さんへのおすすめチャンネル**",
                "自己紹介から、あなたに合いそうなチャンネルをピックアップしました！",
                "",
            ]
            if summary:
                post_lines.append(f"_{summary}_")
                post_lines.append("")
            post_lines.append("📌 **おすすめ**")
            for idx, rid in enumerate(recommended_ids[:10], start=1):
                post_lines.append(f"{idx}. <#{rid}>")

            # Try to post via webhook (ラボちゃん persona)
            posted = False
            if thread.parent and isinstance(thread.parent, discord.ForumChannel):
                webhook = await self._get_or_create_webhook(thread.parent)
                if webhook:
                    try:
                        await webhook.send(
                            "\n".join(post_lines),
                            username=LABCHAN_DISPLAY_NAME,
                            thread=thread,
                        )
                        lines.append("")
                        lines.append(f"✅ ラボちゃんとして投稿しました: {thread.jump_url}")
                        posted = True
                    except Exception as exc:
                        self._log(f"webhook post failed in test: {exc}")

            # Fallback to direct send
            if not posted:
                try:
                    await thread.send("\n".join(post_lines))
                    lines.append("")
                    lines.append(f"✅ スレッドに投稿しました: {thread.jump_url}")
                except Exception as exc:
                    lines.append("")
                    lines.append(f"⚠️ 投稿に失敗: {exc}")

            await self._send_followup(
                interaction,
                "\n".join(lines),
                ephemeral=True,
            )

        self._log(
            f"test_recommend user={member.id} tags={tags} "
            f"dry_run={dry_run} recommended={recommended_ids[:10]}"
        )

    async def _send_welcome_dm(self, member: discord.User | discord.Member) -> None:
        embed = discord.Embed(
            title="🎉 AGIラボへようこそ！",
            description=(
                "AGIラボDiscordに参加いただきありがとうございます！\n\n"
                "まずは自己紹介を作成して、他のメンバーに自分を知ってもらいましょう。\n"
                "以下のボタンから簡単に作成できます。"
            ),
        )
        embed.add_field(
            name="📝 プロフィール作成",
            value="`/onboard` コマンドでプロフィールカードを作成できます。",
            inline=False,
        )
        embed.add_field(
            name="🏷️ タイプを選ぶ",
            value="\n".join(f"- **{k}**: {v}" for k, v in ARCHETYPE_DESCRIPTIONS.items()),
            inline=False,
        )

        await member.send(embed=embed, view=OnboardView(self))

    async def _check_authorized(self, interaction: discord.Interaction) -> bool:
        if not self.config.allowed_role_ids:
            return True
        member = await self._get_member(interaction)
        if member is None:
            return False
        return any(role.id in self.config.allowed_role_ids for role in member.roles)

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not self.config.admin_role_ids:
            return False
        member = await self._get_member(interaction)
        if member is None:
            return False
        return any(role.id in self.config.admin_role_ids for role in member.roles)

    async def _get_member(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        guild = interaction.guild
        if guild is None:
            guild = self._client.get_guild(self.config.guild_id) if self._client else None

        if guild is None and self._client:
            try:
                guild = await self._client.fetch_guild(self.config.guild_id)
            except Exception:
                guild = None

        if guild is None:
            return None

        user = interaction.user
        if user is None or not hasattr(user, "id"):
            return None

        member = guild.get_member(user.id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user.id)
        except Exception:
            return None

    async def _defer(self, interaction: discord.Interaction) -> bool:
        """Defer the interaction. Returns True if successful."""
        ephemeral = interaction.guild_id is not None
        if interaction.response.is_done():
            return True  # Already responded, followup should work
        try:
            await interaction.response.defer(ephemeral=ephemeral)
            return True
        except discord.HTTPException:
            # Try without defer - respond directly later
            return False

    async def _send_interaction_message(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        ephemeral = ephemeral and interaction.guild_id is not None
        if interaction.response.is_done():
            if view:
                await interaction.followup.send(content, ephemeral=ephemeral, view=view)
            else:
                await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            if view:
                await interaction.response.send_message(content, ephemeral=ephemeral, view=view)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)

    async def _send_followup(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        ephemeral = ephemeral and interaction.guild_id is not None
        try:
            if view:
                await interaction.followup.send(content, ephemeral=ephemeral, view=view)
            else:
                await interaction.followup.send(content, ephemeral=ephemeral)
        except discord.HTTPException:
            # Fallback: try direct response if followup fails
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(content, ephemeral=ephemeral)
            except discord.HTTPException:
                self._log(f"Failed to send message: {content[:50]}")

    async def _get_forum_channel(self) -> Optional[discord.ForumChannel]:
        if not self._client:
            return None
        forum = self._client.get_channel(self.config.profile_forum_channel_id)
        if forum is None:
            try:
                forum = await self._client.fetch_channel(self.config.profile_forum_channel_id)
            except Exception:
                return None
        if not isinstance(forum, discord.ForumChannel):
            return None
        return forum

    async def _fetch_thread(self, thread_id: int) -> Optional[discord.Thread]:
        if not self._client:
            return None
        thread = self._client.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self._client.fetch_channel(thread_id)
            except Exception:
                return None
        if isinstance(thread, discord.Thread):
            return thread
        return None

    async def _fetch_thread_starter_message(
        self, thread: discord.Thread
    ) -> Optional[discord.Message]:
        try:
            return await thread.fetch_message(thread.id)
        except Exception:
            return None

    async def _fetch_text_channel(self, channel_id: int) -> Optional[discord.TextChannel]:
        if not self._client:
            return None
        channel = self._client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(channel_id)
            except Exception:
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    def _log(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{timestamp} {message}"
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass
        self._enqueue_log_line(line)
        if self._debug:
            print(f"[LabOnboarder] {message}", flush=True)

    def _tail_log_lines(self, count: int) -> str:
        if not self._log_path.exists():
            return ""
        buf: deque[str] = deque(maxlen=count)
        with self._log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                buf.append(line.rstrip("\n"))
        return "\n".join(buf)

    def _enqueue_log_line(self, line: str) -> None:
        if not self.config.log_channel_id or self._log_queue is None:
            return
        try:
            if self._log_queue.full():
                try:
                    self._log_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._log_queue.put_nowait(line)
        except Exception:
            return

    async def _log_sender_loop(self) -> None:
        if not self._client or not self.config.log_channel_id:
            return
        try:
            await self._client.wait_until_ready()
        except Exception:
            return

        channel = await self._fetch_log_channel(self.config.log_channel_id)
        if channel is None:
            return

        while True:
            try:
                line = await self._log_queue.get()
            except Exception:
                await asyncio.sleep(1)
                continue
            lines = [line]
            try:
                while len(lines) < 20:
                    lines.append(self._log_queue.get_nowait())
            except asyncio.QueueEmpty:
                pass

            payload = "\n".join(lines)
            for chunk in _split_log_chunks(payload, 1900):
                try:
                    await channel.send(f"```{chunk}```")
                except Exception:
                    break
            await asyncio.sleep(0.4)

    async def _fetch_log_channel(
        self,
        channel_id: int,
    ) -> Optional[discord.abc.Messageable]:
        if not self._client:
            return None
        channel = self._client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(channel_id)
            except Exception:
                return None
        if isinstance(channel, discord.abc.Messageable):
            return channel
        return None


# ============================================================================
# UI Views and Modals
# ============================================================================


class OnboardView(discord.ui.View):
    """Button view to open the onboard modal."""

    def __init__(self, agent: LabOnboarderAgent) -> None:
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(
        label="プロフィールを作成",
        style=discord.ButtonStyle.primary,
        custom_id="lab_onboarder:open_modal",
    )
    async def open_modal(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(OnboardModal(self.agent, interaction.user))


class OnboardModal(discord.ui.Modal):
    """Simplified 4-field modal for creating a profile."""

    def __init__(
        self,
        agent: LabOnboarderAgent,
        user: Optional[discord.User | discord.Member] = None,
    ) -> None:
        super().__init__(title="プロフィール作成", custom_id="lab_onboarder:onboard_modal")
        self.agent = agent

        # Get existing profile for defaults
        existing: Optional[ProfileRecord] = None
        default_name = ""
        if user is not None:
            existing = self.agent.store.get_profile(user.id)
            default_name = user.display_name

        # Field 1: Display name (required)
        self.display_name = discord.ui.TextInput(
            label="表示名",
            placeholder="例: 山田太郎",
            max_length=50,
            required=True,
            default=existing.display_name if existing else default_name,
        )

        # Field 2: Introduction (optional)
        self.introduction = discord.ui.TextInput(
            label="自己紹介文",
            placeholder="例: AIツールを使った業務効率化に興味があります",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False,
            default=existing.introduction if existing else None,
        )

        # Field 3: Archetype (required) - show all 8 in placeholder
        archetype_options = " / ".join(sorted(ARCHETYPES))
        self.archetype = discord.ui.TextInput(
            label="タイプ（下記8種から1つ選んで入力）",
            placeholder=archetype_options,
            max_length=20,
            required=True,
            default=existing.archetype if existing else None,
        )

        # Field 4: X URL (optional)
        self.x_profile_url = discord.ui.TextInput(
            label="X（旧Twitter）リンク（任意）",
            placeholder="例: https://x.com/your_handle",
            max_length=200,
            required=False,
            default=existing.x_profile_url if existing else None,
        )

        self.add_item(self.display_name)
        self.add_item(self.introduction)
        self.add_item(self.archetype)
        self.add_item(self.x_profile_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.agent._log(f"onboard modal submit user={interaction.user.id}")
            await self.agent._handle_onboard(
                interaction,
                self.display_name.value,
                self.introduction.value,
                self.archetype.value,
                self.x_profile_url.value,
            )
        except Exception as exc:
            self.agent._log(f"onboard modal error user={interaction.user.id} err={exc}")
            try:
                await self.agent._send_followup(
                    interaction,
                    "⚠️ 送信に失敗しました。もう一度お試しください。",
                    ephemeral=True,
                )
            except Exception:
                pass  # Interaction may have expired


class ProfileEditModal(discord.ui.Modal):
    """Modal for editing an existing profile."""

    def __init__(
        self,
        agent: LabOnboarderAgent,
        user: discord.User | discord.Member,
    ) -> None:
        super().__init__(title="プロフィール編集", custom_id="lab_onboarder:edit_modal")
        self.agent = agent

        existing = self.agent.store.get_profile(user.id)

        self.display_name = discord.ui.TextInput(
            label="表示名",
            placeholder="例: 山田太郎",
            max_length=50,
            required=True,
            default=existing.display_name if existing else user.display_name,
        )

        self.introduction = discord.ui.TextInput(
            label="自己紹介文",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False,
            default=existing.introduction if existing else None,
        )

        archetype_options = " / ".join(sorted(ARCHETYPES))
        self.archetype = discord.ui.TextInput(
            label="タイプ（下記8種から1つ選んで入力）",
            placeholder=archetype_options,
            max_length=20,
            required=True,
            default=existing.archetype if existing else None,
        )

        self.x_profile_url = discord.ui.TextInput(
            label="X（旧Twitter）リンク（任意）",
            placeholder="例: https://x.com/your_handle",
            max_length=200,
            required=False,
            default=existing.x_profile_url if existing else None,
        )

        self.add_item(self.display_name)
        self.add_item(self.introduction)
        self.add_item(self.archetype)
        self.add_item(self.x_profile_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.agent._log(f"edit modal submit user={interaction.user.id}")
            await self.agent._handle_onboard(
                interaction,
                self.display_name.value,
                self.introduction.value,
                self.archetype.value,
                self.x_profile_url.value,
            )
        except Exception as exc:
            self.agent._log(f"edit modal error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ 保存に失敗しました。もう一度お試しください。",
                ephemeral=True,
            )


# ============================================================================
# Helper Functions
# ============================================================================


def _normalize_archetype(value: str) -> Optional[str]:
    """Normalize archetype input to canonical form."""
    text = value.strip()
    if not text:
        return None
    for arche in ARCHETYPES:
        if arche.lower() == text.lower():
            return arche
    return None


def _strip_markdown_code_block(text: str) -> str:
    """Strip markdown code block formatting from LLM response."""
    text = text.strip()
    # Remove ```json or ``` at start
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    # Remove ``` at end
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _parse_recommendation_response(text: str) -> tuple[Optional[str], List[int]]:
    """Parse LLM response into (summary, channel_ids).

    Handles two formats:
    - New format: {"summary": "...", "channels": [id1, id2, ...]}
    - Legacy format: [id1, id2, ...] (returns None for summary)
    """
    text = _strip_markdown_code_block(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: extract IDs from malformed response
        numbers = re.findall(r'\d{10,20}', text)
        return None, [int(n) for n in numbers[:10]]

    # New format: {"summary": ..., "channels": [...]}
    if isinstance(data, dict):
        summary = data.get("summary")
        if isinstance(summary, str):
            summary = summary.strip() or None
        else:
            summary = None

        raw_ids = data.get("channels", [])
        if not isinstance(raw_ids, list):
            raw_ids = []

        result = []
        for item in raw_ids:
            try:
                result.append(int(item))
            except (ValueError, TypeError):
                continue
        return summary, result

    # Legacy format: [id1, id2, ...]
    if isinstance(data, list):
        result = []
        for item in data:
            try:
                result.append(int(item))
            except (ValueError, TypeError):
                continue
        return None, result

    return None, []


def _normalize_x_url(url: str) -> Optional[str]:
    """Normalize X/Twitter URL to https://x.com/handle format."""
    url = url.strip()
    if not url:
        return None

    # Accept x.com and twitter.com URLs
    patterns = [
        r"https?://(?:www\.)?x\.com/([a-zA-Z0-9_]+)",
        r"https?://(?:www\.)?twitter\.com/([a-zA-Z0-9_]+)",
    ]

    for pattern in patterns:
        match = re.match(pattern, url, re.IGNORECASE)
        if match:
            handle = match.group(1)
            return f"https://x.com/{handle}"

    # If it looks like just a handle
    if re.match(r"^@?[a-zA-Z0-9_]+$", url):
        handle = url.lstrip("@")
        return f"https://x.com/{handle}"

    return None


def _extract_x_handle(url: str) -> Optional[str]:
    """Extract handle from X URL."""
    match = re.search(r"x\.com/([a-zA-Z0-9_]+)", url)
    if match:
        return match.group(1)
    return None


def _split_log_chunks(text: str, limit: int) -> List[str]:
    """Split log text into chunks for Discord message limits."""
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
        chunk = remaining[:cut]
        chunks.append(chunk)
        remaining = remaining[cut:].lstrip("\n")
    return chunks
