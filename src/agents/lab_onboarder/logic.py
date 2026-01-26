"""
Lab Onboarder Agent - Simplified Profile Card System

This agent helps new members create profile cards for the AGI Lab Discord community.
Users input their name, introduction, archetype, and optionally their X (Twitter) link.
If the introduction is left empty, the LLM generates one based on the archetype.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from src.agents.daily_reporter.storage import DailyDigestStore
from src.agents.quiz_master.scoring import GeminiLLM
from src.core import config as core_config
from src.core.agent_base import BaseAgent

from src.agents.lab_onboarder.config import LabOnboarderConfig, load_config
from src.agents.lab_onboarder.storage import ProfileRecord, ProfileStore


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

# Keywords for channel recommendations based on archetype
ARCHETYPE_KEYWORDS = {
    "Builder": ["build", "dev", "tools", "prototype"],
    "Researcher": ["research", "paper", "analysis"],
    "Operator": ["ops", "workflow", "automation"],
    "Curator": ["news", "summary", "tools"],
    "Connector": ["community", "event", "collab"],
    "Critic": ["debate", "review", "risk"],
    "Creative": ["design", "art", "story"],
    "Strategist": ["product", "market", "strategy"],
}

RECOMMENDATION_DAYS = 14
RECOMMENDATION_MAX = 5

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
        self._digest_store = DailyDigestStore(Path("data/daily_reporter/digests.sqlite"))
        self._client: Optional[discord.Client] = None
        self._commands_registered = False
        self._debug = self.config.debug
        self._log_path = self.config.log_path
        self._log_queue: Optional[asyncio.Queue[str]] = None
        self._log_sender_task: Optional[asyncio.Task[None]] = None

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
            result = await llm.agenerate(prompt)
            if result and result.strip():
                return result.strip()
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
        if thread_id is None:
            return
        thread = await self._fetch_thread(thread_id)
        if thread is None:
            return

        digests = await self._ensure_digest_data()
        if not digests:
            self._log("recommendations skipped: no digests")
            return

        archetype = card.archetype or "Curator"
        keywords = ARCHETYPE_KEYWORDS.get(archetype, ARCHETYPE_KEYWORDS["Curator"])
        channel_scores: Dict[int, float] = {}
        channel_snippets: Dict[int, str] = {}

        now = datetime.now(timezone.utc)
        for digest in digests:
            try:
                created = datetime.fromisoformat(digest.created_at.replace("Z", "+00:00"))
            except Exception:
                created = now
            days_ago = (now - created).days
            recency_weight = 1.0 if days_ago <= 7 else 0.7
            content_lower = digest.content.lower()
            keyword_hits = sum(1 for kw in keywords if kw in content_lower)
            score = (1 + keyword_hits) * recency_weight

            for channel_id in digest.extracted_channels:
                channel_scores[channel_id] = channel_scores.get(channel_id, 0.0) + score
                if channel_id not in channel_snippets:
                    snippet = _extract_channel_snippet(digest.content, channel_id)
                    if snippet:
                        channel_snippets[channel_id] = snippet

        if not channel_scores:
            self._log("recommendations skipped: no channel scores")
            return

        ranked = sorted(channel_scores.items(), key=lambda item: item[1], reverse=True)
        top = ranked[: min(RECOMMENDATION_MAX, len(ranked))]
        if not top:
            return

        lines = [
            "📌 **おすすめチャンネル（過去14日の日報ベース）**",
            f"対象: <@{user.id}> / タイプ={archetype}",
        ]
        for idx, (channel_id, _) in enumerate(top, start=1):
            mention = f"<#{channel_id}>"
            snippet = channel_snippets.get(channel_id, "")
            if snippet:
                lines.append(f"{idx}. {mention} — {snippet}")
            else:
                lines.append(f"{idx}. {mention}")

        try:
            await thread.send("\n".join(lines))
        except Exception as exc:
            self._log(f"recommendations post failed user={user.id} err={exc}")

    async def _ensure_digest_data(self) -> List[Any]:
        digests = self._digest_store.get_recent_digests(RECOMMENDATION_DAYS)
        if digests:
            return digests

        channel_id = core_config.TARGET_CHANNEL_ID
        if not channel_id:
            self._log("recommendations backfill skipped: DISCORD_CHANNEL_ID not set")
            return []

        channel = await self._fetch_text_channel(int(channel_id))
        if channel is None:
            self._log("recommendations backfill skipped: channel not found")
            return []

        threshold = datetime.now(timezone.utc) - timedelta(days=RECOMMENDATION_DAYS)
        try:
            async for msg in channel.history(after=threshold, limit=None):
                if not getattr(msg, "webhook_id", None) and not msg.author.bot:
                    continue
                if "📅 **今日のラボ日誌**" not in (msg.content or ""):
                    continue
                channel_ids = _extract_channel_ids(msg.content)
                created_at = (
                    msg.created_at.isoformat()
                    if getattr(msg, "created_at", None)
                    else datetime.now(timezone.utc).isoformat()
                )
                self._digest_store.upsert_digest(
                    message_id=msg.id,
                    channel_id=channel.id,
                    created_at=created_at,
                    content=msg.content,
                    extracted_channels=channel_ids,
                )
        except Exception as exc:
            self._log(f"recommendations backfill failed err={exc}")
            return []

        return self._digest_store.get_recent_digests(RECOMMENDATION_DAYS)

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


def _extract_channel_ids(content: str) -> List[int]:
    """Extract Discord channel IDs from message content."""
    if not content:
        return []
    pattern = re.compile(r"https://discord\.com/channels/\d+/(\d+)/\d+")
    ids: List[int] = []
    for match in pattern.finditer(content):
        try:
            ids.append(int(match.group(1)))
        except ValueError:
            continue
    return ids


def _extract_channel_snippet(content: str, channel_id: int) -> str:
    """Extract a snippet of content related to a channel."""
    if not content:
        return ""
    token = f"/{channel_id}/"
    for line in content.splitlines():
        if token not in line:
            continue
        cleaned = re.sub(r"https://discord\.com/channels/\d+/\d+/\d+", "", line)
        cleaned = cleaned.strip(" -•\t")
        if cleaned:
            return cleaned[:120]
    return ""


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
