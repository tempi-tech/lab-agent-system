from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
import httpx
from discord import app_commands
from urllib.parse import quote, urlparse

from src.agents.quiz_master.scoring import GeminiLLM, safe_json_loads
from src.core import config as core_config
from src.agents.daily_reporter.storage import DailyDigestStore

from src.core.agent_base import BaseAgent
from src.agents.lab_onboarder.config import LabOnboarderConfig, load_config
from src.agents.lab_onboarder.storage import ProfileRecord, ProfileStore


PROFILE_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "AGI Lab Profile Card",
    "type": "object",
    "properties": {
        "displayName": {"type": "string", "description": "表示名（推定でもOK）"},
        "handle": {"type": "string", "description": "Xの@handle（分かれば）"},
        "oneLiner": {"type": "string", "description": "1行自己紹介（短く）"},
        "archetype": {
            "type": "string",
            "description": "AGIラボの8タイプのうち最も近いものを1つ",
            "enum": [
                "Builder",
                "Researcher",
                "Operator",
                "Curator",
                "Connector",
                "Critic",
                "Creative",
                "Strategist",
            ],
        },
        "topics": {
            "type": "array",
            "items": {"type": "string"},
            "description": "関心テーマ（5〜10個）",
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "触ってそうなツール（0〜8個）",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "強み（3つ）",
        },
        "cautions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "やりがちな注意点（2つ、ユーモアOK）",
        },
        "lookingFor": {
            "type": "array",
            "items": {"type": "string"},
            "description": "求める繋がり（2〜4個）",
        },
        "conversationStarters": {
            "type": "array",
            "items": {"type": "string"},
            "description": "話しかけるきっかけ（3つ）",
        },
        "recommendedChannels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "おすすめ導線（例: random / tools / topics）",
        },
    },
    "required": ["displayName", "archetype", "topics", "conversationStarters"],
}
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

PROFILE_QUERY = """
あなたはAGIラボDiscordのオンボーディング用に「プロフィールカード」を作っています。
入力URLは主にX（旧Twitter）のプロフィールを想定しますが、GitHub/個人サイト/note等の公開プロフィールURLでも動きます。公開情報から推測して構いません。
出力は必ずschemaに一致するJSONのみ。日本語で書くこと（archetypeはenum通り英語）。
topics/tools/lookingFor/strengths/cautions/conversationStarters/recommendedChannels は簡潔な短文。
""".strip()

LIVECRAWL_TIMEOUT_MS = 15000
SEARCH_FALLBACK_RESULTS = 5
SEARCH_FALLBACK_MAX_URLS = 5
CHATGPT_PREFILL_BASE = "https://chatgpt.com/?tool=canmore&prompt="
X_API_BASE_URL = "https://api.x.com/2"
X_USER_FIELDS = "description,public_metrics,created_at,location,url,verified,profile_image_url"
X_TWEET_FIELDS = "created_at,public_metrics,lang"
X_TWEET_MAX_RESULTS = 10
RECOMMENDATION_DAYS = 14
RECOMMENDATION_MAX = 5
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
CHANNEL_LINK_PATTERN = re.compile(r"https://discord\.com/channels/\d+/(\d+)/\d+")
AI_HELP_PROMPT = (
    "自己紹介200-300字と話題3つを作成。"
    "Q:何をしてる/興味/話したいこと"
)


@dataclass
class ProfileCard:
    display_name: str
    handle: Optional[str]
    one_liner: Optional[str]
    archetype: Optional[str]
    topics: List[str]
    tools: List[str]
    strengths: List[str]
    cautions: List[str]
    looking_for: List[str]
    conversation_starters: List[str]
    recommended_channels: List[str]


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
        self._x_api_cache_ttl = max(5, self.config.x_api_cache_ttl_minutes)

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
                description="プロフィールURLを貼るだけでプロフィールカードを作成/更新します",
                guild=guild,
            )
            @app_commands.describe(x_url="例: https://x.com/your_handle")
            async def onboard_cmd(interaction: discord.Interaction, x_url: str) -> None:
                await self._handle_onboard(interaction, x_url, None, None)

            @client.tree.command(
                name="profile_edit",
                description="プロフィールカードを編集します（手入力UI）",
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
                await self._send_interaction_message(
                    interaction,
                    "編集したい項目を選んでください。",
                    ephemeral=True,
                    view=ProfileEditMenuView(self),
                )

            @client.tree.command(
                name="match",
                description="あなたと相性が良さそうな人を2〜3名提案します",
                guild=guild,
            )
            @app_commands.describe(count="候補の人数（1〜5）")
            async def match_cmd(interaction: discord.Interaction, count: Optional[int] = None) -> None:
                await self._handle_match(interaction, count)

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
                name="lab_debug_welcome",
                description="（管理者用）初期メッセージDMを送信します",
                guild=guild,
            )
            @app_commands.describe(user="送信先ユーザー（未指定なら自分）")
            async def debug_welcome_cmd(
                interaction: discord.Interaction,
                user: Optional[discord.Member] = None,
            ) -> None:
                await self._handle_debug_dm(interaction, user)

            @client.tree.command(
                name="lab_debug_welcome_admins",
                description="（管理者用）agi-lab-admin全員に初期DMを送信",
                guild=guild,
            )
            async def debug_welcome_admins_cmd(
                interaction: discord.Interaction,
            ) -> None:
                await self._handle_debug_welcome_admins(interaction)

            @client.tree.command(
                name="lab_debug_onboard",
                description="（管理者用）指定ユーザーのプロフィールカードを作成/更新",
                guild=guild,
            )
            @app_commands.describe(
                user="対象ユーザー（未指定なら自分）",
                x_url="プロフィールURL（X / GitHub / note など）",
            )
            async def debug_onboard_cmd(
                interaction: discord.Interaction,
                x_url: str,
                user: Optional[discord.Member] = None,
            ) -> None:
                await self._handle_debug_onboard(interaction, user, x_url)

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
        profile_url: Optional[str],
        profile_text: Optional[str],
        x_profile_url: Optional[str],
    ) -> None:
        self._log(
            "onboard request "
            f"user={getattr(interaction.user, 'id', 'unknown')} "
            f"guild={interaction.guild_id} "
            f"url={'yes' if profile_url else 'no'} "
            f"text_len={len((profile_text or '').strip())}"
        )
        await self._defer(interaction)
        if not await self._check_authorized(interaction):
            self._log(
                "onboard unauthorized "
                f"user={getattr(interaction.user, 'id', 'unknown')} "
                f"guild={interaction.guild_id}"
            )
            await self._send_followup(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return
        raw_x_profile_url = (x_profile_url or "").strip()
        profile_url = _normalize_url(profile_url) if profile_url else None
        profile_text = (profile_text or "").strip()
        existing = self.store.get_profile(interaction.user.id)
        normalized_x = _normalize_url(raw_x_profile_url) if raw_x_profile_url else None
        if raw_x_profile_url and normalized_x is None:
            self._log(
                "onboard invalid_x_url "
                f"user={getattr(interaction.user, 'id', 'unknown')}"
            )
            await self._send_followup(
                interaction,
                "⚠️ X URLの形式が正しくありません。`https://x.com/username` のように入力してください。",
                ephemeral=True,
            )
            return
        x_profile_url = normalized_x
        if x_profile_url is None and existing is not None:
            x_profile_url = existing.x_profile_url

        if not profile_url and not profile_text:
            self._log(
                "onboard missing_input "
                f"user={getattr(interaction.user, 'id', 'unknown')}"
            )
            await self._send_followup(
                interaction,
                self._ai_help_message(
                    "URLか自己紹介テキストを入力してください。"
                ),
                ephemeral=True,
                view=OnboardView(self),
            )
            return

        if profile_url is None and profile_text:
            # OK: text-only mode
            pass
        elif profile_url is None:
            self._log(
                "onboard invalid_url "
                f"user={getattr(interaction.user, 'id', 'unknown')}"
            )
            await self._send_followup(
                interaction,
                self._ai_help_message(
                    "URLの形式が正しくありません。`https://note.com/...` のように入力してください。"
                ),
                ephemeral=True,
                view=OnboardView(self),
            )
            return
        elif profile_url and _is_x_url(profile_url) and not profile_text:
            self._log(
                "onboard x_requires_text "
                f"user={getattr(interaction.user, 'id', 'unknown')}"
            )
            await self._send_followup(
                interaction,
                self._ai_help_message(
                    "Xは取得できないため、自己紹介テキストを入力してください。"
                ),
                ephemeral=True,
                view=OnboardView(self),
            )
            return

        try:
            self._log(
                f"onboard start user={interaction.user.id} url={profile_url or 'none'}"
            )
            card, raw = await self._build_profile(
                profile_url=profile_url,
                profile_text=profile_text,
            )
            thread_id, message_id = await self._upsert_forum_thread(
                interaction.user,
                profile_url or "",
                x_profile_url,
                card,
            )

            self.store.upsert_profile(
                discord_user_id=interaction.user.id,
                profile_url=profile_url or "",
                x_profile_url=x_profile_url,
                handle=card.handle,
                display_name=card.display_name,
                one_liner=card.one_liner,
                archetype=card.archetype,
                topics=card.topics,
                tools=card.tools,
                strengths=card.strengths,
                cautions=card.cautions,
                looking_for=card.looking_for,
                conversation_starters=card.conversation_starters,
                recommended_channels=card.recommended_channels,
                raw_summary_json=raw,
                forum_thread_id=thread_id,
                forum_message_id=message_id,
            )
            await self._post_channel_recommendations(
                interaction.user,
                thread_id,
                card,
            )

            await self._send_followup(
                interaction,
                "✅ プロフィールカードを作成/更新しました。#profiles のスレッドに投稿しました！",
                ephemeral=True,
            )
        except Exception as exc:
            self._log(f"onboard error user={interaction.user.id} err={exc}")
            message = f"⚠️ エラー: {exc}"
            view = OnboardView(self) if "自己紹介" in message or "URL" in message else None
            await self._send_followup(
                interaction,
                message,
                ephemeral=True,
                view=view,
            )

    async def _handle_match(self, interaction: discord.Interaction, count: Optional[int]) -> None:
        if not await self._check_authorized(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        await self._defer(interaction)

        me = self.store.get_profile(interaction.user.id)
        if not me:
            await self._send_followup(
                interaction,
                "まだプロフィールがありません。先に `/onboard` で作成してください。",
                ephemeral=True,
            )
            return

        if not me.forum_thread_id:
            await self._send_followup(
                interaction,
                "プロフィールのスレッドが見つかりません。先に `/onboard` で作成してください。",
                ephemeral=True,
            )
            return

        suggestions = self._suggest_matches(me, max(1, min(5, count or 3)))
        if not suggestions:
            await self._send_followup(
                interaction,
                "まだ候補がいません（他の人のプロフィール登録を待ってね）。",
                ephemeral=True,
            )
            return

        thread = await self._fetch_thread(me.forum_thread_id)
        if thread is None:
            await self._send_followup(
                interaction,
                "プロフィールスレッドが見つかりませんでした。運営に連絡してください。",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"🤝 **相性の良さそうな人（{now}）**",
            "(topics重なりベースの暫定提案)",
            "",
        ]
        for idx, (profile, score) in enumerate(suggestions, start=1):
            name = profile.display_name or profile.handle or str(profile.discord_user_id)
            handle = f"(@{profile.handle.lstrip('@')})" if profile.handle else ""
            arche = f" / {profile.archetype}" if profile.archetype else ""
            topics = " ".join(f"#{t}" for t in (profile.topics or [])[:5])
            thread_link = f" <#{profile.forum_thread_id}>" if profile.forum_thread_id else ""
            lines.append(
                f"{idx}. <@{profile.discord_user_id}> {handle}{arche} score={score:.2f}{thread_link}\n"
                f"   {topics}"
            )

        await thread.send("\n".join(lines))
        await self._send_followup(
            interaction,
            "✅ #profiles のスレッドにマッチ候補を追記しました。",
            ephemeral=True,
        )
        self._log(f"match posted user={interaction.user.id} count={len(suggestions)}")

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

    async def _handle_debug_welcome_admins(self, interaction: discord.Interaction) -> None:
        if not await self._check_admin(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await self._send_interaction_message(
                interaction,
                "サーバー情報が取得できませんでした。",
                ephemeral=True,
            )
            return

        members: Dict[int, discord.Member] = {}
        for role_id in self.config.admin_role_ids:
            role = guild.get_role(role_id)
            if role is None:
                try:
                    role = await guild.fetch_role(role_id)
                except Exception:
                    role = None
            if role is None:
                continue
            for member in role.members:
                members[member.id] = member

        if not members:
            await self._send_interaction_message(
                interaction,
                "管理者ロールのメンバーが見つかりませんでした。",
                ephemeral=True,
            )
            return

        await self._defer(interaction)

        success = 0
        failures: List[str] = []
        for member in members.values():
            if member.bot:
                continue
            try:
                await self._send_welcome_dm(member)
                success += 1
            except Exception as exc:
                failures.append(f"{member}: {exc}")

        message = f"✅ 管理者にDM送信しました: {success}件"
        if failures:
            message += f"（失敗 {len(failures)}件）"
            self._log("welcome_admins failures: " + " / ".join(failures))

        await self._send_followup(
            interaction,
            message,
            ephemeral=True,
        )

    async def _handle_debug_onboard(
        self,
        interaction: discord.Interaction,
        target: Optional[discord.Member],
        profile_url: str,
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

        profile_url = _normalize_url(profile_url) if profile_url else None
        if not profile_url:
            await self._send_interaction_message(
                interaction,
                "URLの形式が正しくありません。`https://x.com/your_handle` のように入力してください。",
                ephemeral=True,
            )
            return

        await self._defer(interaction)

        try:
            self._log(
                f"debug_onboard start by={interaction.user.id} target={member.id} url={profile_url}"
            )
            card, raw = await self._build_profile(
                profile_url=profile_url,
                profile_text=None,
            )
            thread_id, message_id = await self._upsert_forum_thread(
                member,
                profile_url,
                None,
                card,
            )

            self.store.upsert_profile(
                discord_user_id=member.id,
                profile_url=profile_url,
                x_profile_url=None,
                handle=card.handle,
                display_name=card.display_name,
                one_liner=card.one_liner,
                archetype=card.archetype,
                topics=card.topics,
                tools=card.tools,
                strengths=card.strengths,
                cautions=card.cautions,
                looking_for=card.looking_for,
                conversation_starters=card.conversation_starters,
                recommended_channels=card.recommended_channels,
                raw_summary_json=raw,
                forum_thread_id=thread_id,
                forum_message_id=message_id,
            )

            await self._send_followup(
                interaction,
                f"✅ {member} のプロフィールカードを作成/更新しました。",
                ephemeral=True,
            )
        except Exception as exc:
            self._log(f"debug_onboard error target={member.id} err={exc}")
            await self._send_followup(
                interaction,
                f"⚠️ エラー: {exc}",
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

        summary = {
            "display_name": record.display_name,
            "handle": record.handle,
            "archetype": record.archetype,
            "topics": record.topics,
            "tools": record.tools,
            "forum_thread_id": record.forum_thread_id,
            "forum_message_id": record.forum_message_id,
            "profile_url": record.profile_url,
            "x_profile_url": record.x_profile_url,
            "updated_at": record.updated_at,
        }
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        if len(text) > 1800:
            text = text[:1800] + "..."
        await self._send_interaction_message(
            interaction,
            f"```json\n{text}\n```",
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
        try:
            content = self._tail_log_lines(count)
        except Exception as exc:
            await self._send_interaction_message(
                interaction,
                f"⚠️ ログ取得に失敗: {exc}",
                ephemeral=True,
            )
            return

        if not content:
            await self._send_interaction_message(
                interaction,
                "ログがまだありません。",
                ephemeral=True,
            )
            return

        if len(content) > 1800:
            content = "(末尾のみ)\n" + content[-1800:]

        await self._send_interaction_message(
            interaction,
            f"```{content}```",
            ephemeral=True,
        )

    def _welcome_message(self) -> str:
        return (
            "ようこそAGIラボへ！\n"
            "まずは **URLか自己紹介テキスト** を入れるだけで、プロフィールカードを作れます。\n\n"
            "👇 下のボタンを押すと入力欄が開きます。\n"
            "- **X以外のURL**（note/GitHub/個人サイトなど）を貼る\n"
            "- もしくは **自己紹介テキスト** を入力する（Xの代わりに推奨）\n\n"
            "自己紹介が大変なら、下の **ChatGPTボタン** で文章を作って貼り付ければOK！\n\n"
            "（DMが難しい場合は、サーバー内で `/onboard` を使ってね）"
        )

    async def _send_welcome_dm(self, member: discord.abc.User) -> None:
        self._log(f"welcome_dm send user={getattr(member, 'id', 'unknown')}")
        await member.send(content=self._welcome_message(), view=OnboardView(self))

    async def _handle_manual_edit(
        self,
        interaction: discord.Interaction,
        *,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
        one_liner: Optional[str] = None,
        archetype: Optional[str] = None,
        profile_url: Optional[str] = None,
        x_profile_url: Optional[str] = None,
        topics: Optional[str] = None,
        tools: Optional[str] = None,
        strengths: Optional[str] = None,
        looking_for: Optional[str] = None,
        conversation_starters: Optional[str] = None,
        recommended_channels: Optional[str] = None,
    ) -> None:
        if not await self._check_authorized(interaction):
            await self._send_interaction_message(
                interaction,
                "このコマンドを使う権限がありません。",
                ephemeral=True,
            )
            return

        record = self.store.get_profile(interaction.user.id)
        if record is None:
            await self._send_interaction_message(
                interaction,
                "プロフィールがまだありません。先に `/onboard` を実行してください。",
                ephemeral=True,
            )
            return

        await self._defer(interaction)

        new_profile_url = _merge_text_field(profile_url, record.profile_url)
        if new_profile_url:
            normalized = _normalize_url(new_profile_url)
            if normalized is None:
                await self._send_followup(
                    interaction,
                    "⚠️ URLの形式が正しくありません。`https://note.com/...` のように入力してください。",
                    ephemeral=True,
                )
                return
            new_profile_url = normalized

        new_x_profile_url = _merge_text_field(x_profile_url, record.x_profile_url)
        if new_x_profile_url:
            normalized_x = _normalize_url(new_x_profile_url)
            if normalized_x is None:
                await self._send_followup(
                    interaction,
                    "⚠️ X URLの形式が正しくありません。`https://x.com/username` のように入力してください。",
                    ephemeral=True,
                )
                return
            new_x_profile_url = normalized_x

        new_display_name = _merge_text_field(display_name, record.display_name)
        new_handle = _merge_text_field(handle, record.handle)
        new_one_liner = _merge_text_field(one_liner, record.one_liner)
        new_archetype = _merge_archetype(archetype, record.archetype)
        if new_archetype is None and archetype:
            await self._send_followup(
                interaction,
                f"⚠️ archetypeは {', '.join(sorted(ARCHETYPES))} のいずれかで入力してください。",
                ephemeral=True,
            )
            return

        new_topics = _merge_list_field(topics, record.topics, strip_hash=True)
        new_tools = _merge_list_field(tools, record.tools, strip_hash=False)
        new_strengths = _merge_list_field(strengths, record.strengths, strip_hash=False)
        new_looking_for = _merge_list_field(looking_for, record.looking_for, strip_hash=False)
        new_starters = _merge_list_field(
            conversation_starters, record.conversation_starters, strip_hash=False
        )
        new_channels = _merge_list_field(
            recommended_channels, record.recommended_channels, strip_hash=False
        )

        card = ProfileCard(
            display_name=new_display_name or "Member",
            handle=new_handle,
            one_liner=new_one_liner,
            archetype=new_archetype,
            topics=new_topics,
            tools=new_tools,
            strengths=new_strengths,
            cautions=record.cautions,
            looking_for=new_looking_for,
            conversation_starters=new_starters,
            recommended_channels=new_channels,
        )

        thread_id, message_id = await self._upsert_forum_thread(
            interaction.user,
            new_profile_url or "",
            new_x_profile_url,
            card,
        )

        raw_summary = json.dumps(
            {
                "manual_edit": True,
                "displayName": card.display_name,
                "handle": card.handle,
                "oneLiner": card.one_liner,
                "archetype": card.archetype,
                "xProfileUrl": new_x_profile_url,
                "topics": card.topics,
                "tools": card.tools,
                "strengths": card.strengths,
                "lookingFor": card.looking_for,
                "conversationStarters": card.conversation_starters,
                "recommendedChannels": card.recommended_channels,
            },
            ensure_ascii=False,
        )

        self.store.upsert_profile(
            discord_user_id=interaction.user.id,
            profile_url=new_profile_url or "",
            x_profile_url=new_x_profile_url,
            handle=card.handle,
            display_name=card.display_name,
            one_liner=card.one_liner,
            archetype=card.archetype,
            topics=card.topics,
            tools=card.tools,
            strengths=card.strengths,
            cautions=card.cautions,
            looking_for=card.looking_for,
            conversation_starters=card.conversation_starters,
            recommended_channels=card.recommended_channels,
            raw_summary_json=raw_summary,
            forum_thread_id=thread_id,
            forum_message_id=message_id,
        )

        await self._post_channel_recommendations(
            interaction.user,
            thread_id,
            card,
        )

        await self._send_followup(
            interaction,
            "✅ プロフィールカードを更新しました。",
            ephemeral=True,
        )

    async def _check_authorized(self, interaction: discord.Interaction) -> bool:
        if not self.config.allowed_role_ids:
            return True
        member = await self._get_member(interaction)
        if member is None:
            self._log(
                "auth member_not_found "
                f"user={getattr(interaction.user, 'id', 'unknown')} "
                f"guild={interaction.guild_id}"
            )
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
            self._log(
                "member_lookup guild_not_found "
                f"user={getattr(interaction.user, 'id', 'unknown')} "
                f"guild={self.config.guild_id}"
            )
            return None

        user = interaction.user
        if user is None or not hasattr(user, "id"):
            return None

        member = guild.get_member(user.id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user.id)
        except Exception as exc:
            self._log(
                "member_lookup fetch_failed "
                f"user={user.id} "
                f"guild={guild.id} "
                f"err={exc}"
            )
            return None

    async def _defer(self, interaction: discord.Interaction) -> None:
        ephemeral = interaction.guild_id is not None
        if interaction.response.is_done():
            return
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

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
            await self._send_with_optional_view(
                interaction.followup.send,
                content,
                ephemeral=ephemeral,
                view=view,
            )
        else:
            await self._send_with_optional_view(
                interaction.response.send_message,
                content,
                ephemeral=ephemeral,
                view=view,
            )

    async def _send_followup(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        ephemeral = ephemeral and interaction.guild_id is not None
        await self._send_with_optional_view(
            interaction.followup.send,
            content,
            ephemeral=ephemeral,
            view=view,
        )

    async def _send_with_optional_view(
        self,
        sender: Any,
        content: str,
        *,
        ephemeral: bool,
        view: Optional[discord.ui.View],
    ) -> None:
        if view is None:
            await sender(content, ephemeral=ephemeral)
        else:
            await sender(content, ephemeral=ephemeral, view=view)

    async def _build_profile(
        self,
        *,
        profile_url: Optional[str],
        profile_text: Optional[str],
    ) -> tuple[ProfileCard, str]:
        url = profile_url
        text = (profile_text or "").strip()

        if text and url:
            if _is_x_url(url):
                return await self._build_profile_from_text(text)
            try:
                return await self._build_profile_from_url_and_text(url, text)
            except Exception as exc:
                self._log(f"url+text failed url={url} err={exc}")
                return await self._build_profile_from_text(text)

        if text:
            return await self._build_profile_from_text(text)

        if url and _is_x_url(url):
            raise RuntimeError("Xは取得できないため、自己紹介テキストを入力してください。")

        if url and self.config.url_context_enabled:
            try:
                return await self._build_profile_with_url_context(url)
            except Exception as exc:
                self._log(f"url context failed url={url} err={exc}")

        if url:
            try:
                return await self._build_profile_from_exa(url)
            except Exception as exc:
                self._log(f"exa failed url={url} err={exc}")

        raise RuntimeError("URLの取得に失敗しました。自己紹介テキストを入力してください。")

    async def _build_profile_from_url_and_text(
        self,
        profile_url: str,
        profile_text: str,
    ) -> tuple[ProfileCard, str]:
        if self.config.url_context_enabled:
            try:
                return await self._build_profile_with_url_context_and_text(
                    profile_url,
                    profile_text,
                )
            except Exception as exc:
                self._log(f"url context+text failed url={profile_url} err={exc}")

        try:
            summary, raw = await self._fetch_exa_summary(profile_url)
            prompt = (
                f"{PROFILE_QUERY}\n\n"
                "以下はURLから抽出した要約と本人の自己紹介テキストです。\n"
                "自己紹介テキストを優先しつつ、URL要約も参考にして統合してください。\n"
                "出力は必ずJSONのみ。\n"
                f"JSON Schema: {json.dumps(PROFILE_SCHEMA, ensure_ascii=False)}\n\n"
                f"URL要約(JSON): {json.dumps(summary, ensure_ascii=False)}\n\n"
                f"自己紹介テキスト:\n{profile_text}\n"
            )
            card_dict = await self._generate_card_with_llm(prompt)
            if card_dict is None:
                raise RuntimeError("LLM failed to merge URL summary and text.")
            card = self._parse_profile_summary(card_dict)
            return card, json.dumps(card_dict, ensure_ascii=False)
        except Exception as exc:
            self._log(f"exa+text failed url={profile_url} err={exc}")

        return await self._build_profile_from_text(profile_text)

    async def _build_profile_from_exa(self, profile_url: str) -> tuple[ProfileCard, str]:
        self._log(f"exa contents preferred url={profile_url}")
        data = await self._exa_contents(
            [profile_url],
            summary_query=PROFILE_QUERY,
            summary_schema=PROFILE_SCHEMA,
            livecrawl="preferred",
        )

        status_tag, status_error = _extract_exa_status_error(data, profile_url)
        if status_error:
            self._log(f"exa contents error url={profile_url} tag={status_tag} err={status_error}")
        if status_error and status_tag == "CRAWL_LIVECRAWL_TIMEOUT":
            self._log(f"exa contents fallback url={profile_url}")
            data = await self._exa_contents(
                [profile_url],
                summary_query=PROFILE_QUERY,
                summary_schema=PROFILE_SCHEMA,
                livecrawl="fallback",
            )
            status_tag, status_error = _extract_exa_status_error(data, profile_url)
            if status_error:
                self._log(
                    f"exa contents fallback error url={profile_url} tag={status_tag} err={status_error}"
                )

        summary, raw_summary = _extract_summary_from_contents(data)
        if summary is None:
            if status_error:
                raise RuntimeError(
                    f"{status_error} URLの取得に失敗しました。自己紹介テキストでもOKです。"
                )
            raise RuntimeError("Exa returned no summary for this URL.")
        card = self._parse_profile_summary(summary)
        return card, raw_summary

    async def _build_profile_with_url_context(self, profile_url: str) -> tuple[ProfileCard, str]:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY が設定されていません。")

        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception as exc:
            raise RuntimeError("Gemini SDK が利用できません。") from exc

        prompt = (
            f"{PROFILE_QUERY}\n\n"
            "次のURLの内容を参照してプロフィールカードを作成してください。\n"
            f"URL: {profile_url}\n\n"
            "出力は必ずJSONのみ。\n"
            f"JSON Schema: {json.dumps(PROFILE_SCHEMA, ensure_ascii=False)}\n"
        )

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())]
        )
        response = client.models.generate_content(
            model=self.config.url_context_model,
            contents=prompt,
            config=config,
        )
        text = getattr(response, "text", "") or ""
        if not text:
            raise RuntimeError("URL Contextの結果が空でした。")
        parsed = safe_json_loads(text)
        if not isinstance(parsed, dict):
            raise RuntimeError("URL Contextの結果をJSONとして解釈できません。")
        card = self._parse_profile_summary(parsed)
        return card, json.dumps(parsed, ensure_ascii=False)

    async def _build_profile_with_url_context_and_text(
        self,
        profile_url: str,
        profile_text: str,
    ) -> tuple[ProfileCard, str]:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY が設定されていません。")

        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception as exc:
            raise RuntimeError("Gemini SDK が利用できません。") from exc

        prompt = (
            f"{PROFILE_QUERY}\n\n"
            "次のURLの内容と、本人の自己紹介テキストを統合してプロフィールカードを作成してください。\n"
            "自己紹介テキストの内容を優先してください。\n"
            f"URL: {profile_url}\n\n"
            f"自己紹介テキスト:\n{profile_text}\n\n"
            "出力は必ずJSONのみ。\n"
            f"JSON Schema: {json.dumps(PROFILE_SCHEMA, ensure_ascii=False)}\n"
        )

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())]
        )
        response = client.models.generate_content(
            model=self.config.url_context_model,
            contents=prompt,
            config=config,
        )
        text = getattr(response, "text", "") or ""
        if not text:
            raise RuntimeError("URL Contextの結果が空でした。")
        parsed = safe_json_loads(text)
        if not isinstance(parsed, dict):
            raise RuntimeError("URL Contextの結果をJSONとして解釈できません。")
        card = self._parse_profile_summary(parsed)
        return card, json.dumps(parsed, ensure_ascii=False)

    async def _build_profile_from_text(self, profile_text: str) -> tuple[ProfileCard, str]:
        prompt = (
            f"{PROFILE_QUERY}\n\n"
            "以下の自己紹介テキストからプロフィールカードを作成してください。\n"
            "出力は必ずJSONのみ。\n"
            f"JSON Schema: {json.dumps(PROFILE_SCHEMA, ensure_ascii=False)}\n\n"
            f"自己紹介テキスト:\n{profile_text}\n"
        )
        card_dict = await self._generate_card_with_llm(prompt)
        if card_dict is None:
            card_dict = {
                "displayName": "Member",
                "archetype": "Curator",
                "topics": ["AI"],
                "conversationStarters": ["最近取り組んでいることを教えてください。"],
                "oneLiner": profile_text[:200],
            }
        card = self._parse_profile_summary(card_dict)
        return card, json.dumps(card_dict, ensure_ascii=False)

    async def _exa_contents(
        self,
        urls: List[str],
        *,
        summary_query: Optional[str] = None,
        summary_schema: Optional[Dict[str, Any]] = None,
        livecrawl: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "urls": urls,
            "text": {"maxCharacters": 6000, "includeHtmlTags": False},
            "extras": {"links": 2},
        }
        if summary_query:
            body["summary"] = {"query": summary_query}
            if summary_schema:
                body["summary"]["schema"] = summary_schema
        if livecrawl:
            body["livecrawl"] = livecrawl
            body["livecrawlTimeout"] = LIVECRAWL_TIMEOUT_MS

        headers = {
            "x-api-key": self.config.exa_api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post("https://api.exa.ai/contents", json=body, headers=headers)
            if resp.status_code >= 400:
                text = resp.text
                raise RuntimeError(f"Exa /contents failed: {resp.status_code} {text}")
            return resp.json()

    async def _fetch_exa_summary(self, profile_url: str) -> tuple[Dict[str, Any], str]:
        self._log(f"exa contents preferred url={profile_url}")
        data = await self._exa_contents(
            [profile_url],
            summary_query=PROFILE_QUERY,
            summary_schema=PROFILE_SCHEMA,
            livecrawl="preferred",
        )

        status_tag, status_error = _extract_exa_status_error(data, profile_url)
        if status_error and status_tag == "CRAWL_LIVECRAWL_TIMEOUT":
            self._log(f"exa contents fallback url={profile_url}")
            data = await self._exa_contents(
                [profile_url],
                summary_query=PROFILE_QUERY,
                summary_schema=PROFILE_SCHEMA,
                livecrawl="fallback",
            )
            status_tag, status_error = _extract_exa_status_error(data, profile_url)

        summary, raw_summary = _extract_summary_from_contents(data)
        if summary is None:
            if status_error:
                raise RuntimeError(f"{status_error} URLの取得に失敗しました。")
            raise RuntimeError("Exa returned no summary for this URL.")
        if isinstance(summary, dict):
            summary_dict = summary
        elif isinstance(summary, str):
            parsed = safe_json_loads(summary)
            summary_dict = parsed if isinstance(parsed, dict) else {"summary": summary}
        else:
            summary_dict = {"summary": str(summary)}
        return summary_dict, raw_summary

    async def _exa_search(
        self,
        query: str,
        *,
        category: Optional[str] = None,
        include_domains: Optional[List[str]] = None,
        num_results: int = SEARCH_FALLBACK_RESULTS,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "query": query,
            "numResults": max(1, num_results),
        }
        if category:
            body["category"] = category
        if include_domains:
            body["includeDomains"] = include_domains

        headers = {
            "x-api-key": self.config.exa_api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("https://api.exa.ai/search", json=body, headers=headers)
            if resp.status_code >= 400:
                text = resp.text
                raise RuntimeError(f"Exa /search failed: {resp.status_code} {text}")
            return resp.json()

    async def _exa_search_x_urls(self, profile_url: str) -> List[str]:
        handle = _extract_x_handle(profile_url)
        if handle:
            query = f"https://x.com/{handle} {handle}"
        else:
            query = profile_url

        include_domains = ["x.com", "twitter.com"]

        self._log(f"exa search query={query} category=tweet")
        data = await self._exa_search(
            query,
            category="tweet",
            include_domains=include_domains,
        )
        urls = _extract_search_urls(data)
        if urls:
            return urls

        self._log(f"exa search query={query} category=all")
        data = await self._exa_search(
            query,
            include_domains=include_domains,
        )
        return _extract_search_urls(data)

    async def _build_profile_from_x_api(self, profile_url: str) -> tuple[ProfileCard, str]:
        token = (self.config.x_api_bearer_token or "").strip()
        if not token:
            raise RuntimeError("X API の Bearer Token が設定されていません。")

        handle = _extract_x_handle(profile_url)
        if not handle:
            raise RuntimeError("Xのユーザー名をURLから取得できません。")

        cached = self.store.get_x_cache(handle)
        if cached and _is_cache_fresh(cached.get("fetched_at"), self._x_api_cache_ttl):
            self._log(f"x api cache hit handle={handle}")
            payload = cached["payload"]
            return await self._card_from_x_payload(payload)

        try:
            user = await self._x_api_get_user(handle, token)
            user_id = user.get("id")
            if not user_id:
                raise RuntimeError("X APIのユーザー情報にIDがありません。")

            tweets = await self._x_api_get_tweets(user_id, token)
            raw_payload = {
                "source": "x_api",
                "user": user,
                "tweets": tweets,
            }
            self.store.set_x_cache(handle, raw_payload)
            return await self._card_from_x_payload(raw_payload)
        except XApiRateLimitError as exc:
            if cached and cached.get("payload"):
                self._log(f"x api rate limited; using stale cache handle={handle}")
                return await self._card_from_x_payload(cached["payload"])
            raise RuntimeError("X APIがレート制限中です。しばらく待ってから再試行してください。") from exc
        except XApiPermissionError as exc:
            raise RuntimeError("X APIの権限が不足しています（Freeティア制限の可能性）。") from exc

    async def _card_from_x_payload(self, payload: Dict[str, Any]) -> tuple[ProfileCard, str]:
        user = payload.get("user") or {}
        tweets = payload.get("tweets") or []
        prompt = _build_x_llm_prompt(user, tweets)
        card_dict = _maybe_get_cached_llm_card(payload)
        if card_dict is None:
            card_dict = await self._generate_card_with_llm(prompt)
        if card_dict is None:
            # LLM might be disabled; fallback to heuristic
            card_dict = _heuristic_card_from_x(user, tweets)
        card = self._parse_profile_summary(card_dict)
        raw_summary = json.dumps(card_dict, ensure_ascii=False)
        return card, raw_summary

    async def _x_api_get_user(self, handle: str, token: str) -> Dict[str, Any]:
        params = {
            "user.fields": X_USER_FIELDS,
        }
        url = f"{X_API_BASE_URL}/users/by/username/{handle}"
        data = await self._x_api_get(url, token, params=params)
        user = data.get("data")
        if not isinstance(user, dict):
            raise RuntimeError("X API user response is empty.")
        return user

    async def _x_api_get_tweets(self, user_id: str, token: str) -> List[Dict[str, Any]]:
        params = {
            "max_results": X_TWEET_MAX_RESULTS,
            "tweet.fields": X_TWEET_FIELDS,
        }
        url = f"{X_API_BASE_URL}/users/{user_id}/tweets"
        data = await self._x_api_get(url, token, params=params)
        tweets = data.get("data")
        if not isinstance(tweets, list):
            return []
        return tweets

    async def _x_api_get(self, url: str, token: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 429:
                raise XApiRateLimitError(resp.text)
            if resp.status_code == 403:
                raise XApiPermissionError(resp.text)
            if resp.status_code >= 400:
                raise RuntimeError(f"X API error: {resp.status_code} {resp.text}")
            data = resp.json()
        if isinstance(data, dict) and data.get("errors"):
            raise RuntimeError(f"X API error: {data.get('errors')}")
        return data

    async def _generate_card_with_llm(self, prompt: str) -> Optional[Dict[str, Any]]:
        try:
            llm = GeminiLLM(model=self.config.llm_model)
        except Exception as exc:
            self._log(f"llm init failed: {exc}")
            return None

        try:
            result = await llm.generate(prompt, temperature=0.2, max_output_tokens=2048)
            parsed = safe_json_loads(result.text)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception as exc:
            self._log(f"llm generate failed: {exc}")
            return None

    async def _upsert_forum_thread(
        self,
        user: discord.User | discord.Member,
        profile_url: Optional[str],
        x_profile_url: Optional[str],
        card: ProfileCard,
    ) -> tuple[int, int]:
        forum = await self._get_forum_channel()
        if forum is None:
            raise RuntimeError("PROFILE_FORUM_CHANNEL_ID is not a Forum channel.")

        existing = self.store.get_profile(user.id)
        thread_name = self._build_thread_name(user, card)
        content = self._build_thread_content(user.id, profile_url)
        embed = self._make_profile_embed(profile_url or "", x_profile_url, card, user)

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
            f"対象: <@{user.id}> / archetype={archetype}",
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

    def _suggest_matches(
        self,
        me: ProfileRecord,
        count: int,
    ) -> List[tuple[ProfileRecord, float]]:
        others = [p for p in self.store.get_all_profiles() if p.discord_user_id != me.discord_user_id]
        scored = []
        for profile in others:
            topic_score = _jaccard(me.topics, profile.topics)
            tool_score = _jaccard(me.tools, profile.tools)
            archetype_bonus = (
                0.05
                if me.archetype and profile.archetype and me.archetype != profile.archetype
                else 0
            )
            score = (0.75 * topic_score) + (0.25 * tool_score) + archetype_bonus
            scored.append((profile, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:count]

    def _build_thread_name(self, user: discord.User | discord.Member, card: ProfileCard) -> str:
        title_name = card.display_name or user.name
        title_type = f" | {card.archetype}" if card.archetype else ""
        return f"{title_name}{title_type}"[:100]

    async def _fetch_thread_starter_message(
        self,
        thread: discord.Thread,
    ) -> Optional[discord.Message]:
        try:
            async for message in thread.history(limit=1, oldest_first=True):
                return message
        except Exception:
            return None
        return None

    def _build_thread_content(self, user_id: int, profile_url: Optional[str]) -> str:
        return ""

    def _parse_profile_summary(self, summary: Any) -> ProfileCard:
        parsed = None
        if isinstance(summary, str):
            try:
                parsed = json.loads(summary)
            except json.JSONDecodeError:
                parsed = None
        elif isinstance(summary, dict):
            parsed = summary

        if not parsed:
            parsed = {
                "displayName": "Unknown",
                "archetype": "Curator",
                "topics": ["AI"],
                "conversationStarters": ["最近触ってるAIツールありますか？"],
                "oneLiner": str(summary)[:200],
            }

        return ProfileCard(
            display_name=str(parsed.get("displayName") or "Unknown"),
            handle=_opt_str(parsed.get("handle")),
            one_liner=_opt_str(parsed.get("oneLiner")),
            archetype=_opt_str(parsed.get("archetype")),
            topics=_as_list(parsed.get("topics")),
            tools=_as_list(parsed.get("tools")),
            strengths=_as_list(parsed.get("strengths")),
            cautions=_as_list(parsed.get("cautions")),
            looking_for=_as_list(parsed.get("lookingFor")),
            conversation_starters=_as_list(parsed.get("conversationStarters")),
            recommended_channels=_as_list(parsed.get("recommendedChannels")),
        )

    def _make_profile_embed(
        self,
        profile_url: str,
        x_profile_url: Optional[str],
        card: ProfileCard,
        user: discord.User | discord.Member,
    ) -> discord.Embed:
        topics = "  ".join(f"#{t}" for t in (card.topics or [])[:8])
        tools = ", ".join((card.tools or [])[:8])
        starters = "\n".join(
            f"{idx + 1}. {text}" for idx, text in enumerate((card.conversation_starters or [])[:3])
        )

        title = f"{card.display_name}"
        if card.handle:
            title += f" (@{card.handle.lstrip('@')})"
        if card.archetype:
            title += f" — {card.archetype}"

        url = _normalize_url(profile_url)
        mention = f"<@{user.id}>"
        one_liner = card.one_liner or ""
        topics_block = f"**Topics**\n{topics}" if topics else ""
        description_parts = [mention, one_liner, topics_block]
        description = "\n\n".join(part for part in description_parts if part)

        embed = discord.Embed(
            title=title,
            description=description,
            url=url or discord.Embed.Empty,
        )

        if x_profile_url:
            x_link = _normalize_url(x_profile_url)
            if x_link:
                x_label = f"@{card.handle.lstrip('@')}" if card.handle else "Xプロフィール"
                embed.add_field(name="X", value=f"[{x_label}]({x_link})", inline=False)

        if tools:
            embed.add_field(name="Tools", value=tools, inline=False)
        if card.strengths:
            embed.add_field(name="強み", value="\n".join(card.strengths[:3]), inline=True)
        if card.looking_for:
            embed.add_field(name="求める繋がり", value="\n".join(card.looking_for[:4]), inline=True)
        if starters:
            embed.add_field(name="話しかけるきっかけ", value=starters, inline=False)

        try:
            avatar_url = user.display_avatar.url
        except Exception:
            avatar_url = None
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text="Generated by ラボちゃん先輩 (MVP)")
        return embed

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

    def _ai_help_message(self, prefix: Optional[str] = None) -> str:
        lines: List[str] = []
        if prefix:
            lines.append(prefix)
            lines.append("")
        lines.append("自己紹介が大変なら、下のボタンでAIに作ってもらえます。")
        lines.append("プロンプト例（ChatGPT/Grokにそのまま貼り付けてOK）:")
        lines.append(f"```{AI_HELP_PROMPT}```")
        lines.append("生成した文章を「自己紹介テキスト」に貼ってください。")
        return "\n".join(lines)


class OnboardView(discord.ui.View):
    def __init__(self, agent: LabOnboarderAgent) -> None:
        super().__init__(timeout=None)
        self.agent = agent
        self.add_item(
            discord.ui.Button(
                label="ChatGPTで自己紹介を作る",
                style=discord.ButtonStyle.link,
                url=_build_chatgpt_prefill_url(),
                row=1,
            )
        )

    @discord.ui.button(
        label="プロフィール作成（URL/自己紹介）",
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
    def __init__(
        self,
        agent: LabOnboarderAgent,
        user: Optional[discord.User | discord.Member] = None,
    ) -> None:
        super().__init__(title="プロフィール作成（URL/自己紹介）", custom_id="lab_onboarder:onboard_modal")
        self.agent = agent
        default_url = None
        default_x_url = None
        if user is not None:
            existing = self.agent.store.get_profile(user.id)
            if existing and existing.profile_url:
                default_url = existing.profile_url
            if existing and existing.x_profile_url:
                default_x_url = existing.x_profile_url
        self.url_input = discord.ui.TextInput(
            label="プロフィールURL（X以外推奨）",
            placeholder="例: https://note.com/your_name / GitHub / 個人サイト",
            required=False,
            max_length=200,
            default=default_url,
        )
        self.x_url_input = discord.ui.TextInput(
            label="XプロフィールURL（任意）",
            placeholder="例: https://x.com/your_handle",
            required=False,
            max_length=200,
            default=default_x_url,
        )
        self.text_input = discord.ui.TextInput(
            label="自己紹介テキスト（Xの代わりにこちら推奨）",
            style=discord.TextStyle.paragraph,
            placeholder="例: 何をしている人か、関心テーマ、話しかけてほしいこと など",
            required=False,
            max_length=1000,
        )
        self.add_item(self.url_input)
        self.add_item(self.x_url_input)
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.agent._log(f"onboard modal submit user={interaction.user.id}")
            await self.agent._handle_onboard(
                interaction,
                self.url_input.value,
                self.text_input.value,
                self.x_url_input.value,
            )
        except Exception as exc:
            self.agent._log(f"onboard modal error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ 送信に失敗しました。もう一度お試しください。",
                ephemeral=True,
            )


class ProfileEditMenuView(discord.ui.View):
    def __init__(self, agent: LabOnboarderAgent) -> None:
        super().__init__(timeout=300)
        self.agent = agent

    @discord.ui.button(
        label="基本情報",
        style=discord.ButtonStyle.primary,
        custom_id="lab_onboarder:edit_basic",
    )
    async def edit_basic(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(ProfileEditBasicModal(self.agent, interaction.user))

    @discord.ui.button(
        label="リンク",
        style=discord.ButtonStyle.secondary,
        custom_id="lab_onboarder:edit_links",
    )
    async def edit_links(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        try:
            self.agent._log(f"edit_links click user={interaction.user.id}")
            await interaction.response.send_modal(
                ProfileEditLinksModal(self.agent, interaction.user)
            )
        except Exception as exc:
            self.agent._log(f"edit_links error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ リンク編集を開けませんでした。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="トピック/ツール",
        style=discord.ButtonStyle.secondary,
        custom_id="lab_onboarder:edit_topics",
    )
    async def edit_topics(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        try:
            self.agent._log(f"edit_topics click user={interaction.user.id}")
            await interaction.response.send_modal(
                ProfileEditTopicsModal(self.agent, interaction.user)
            )
        except Exception as exc:
            self.agent._log(f"edit_topics error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ トピック/ツールの編集を開けませんでした。もう一度お試しください。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="強み/求める繋がり",
        style=discord.ButtonStyle.secondary,
        custom_id="lab_onboarder:edit_strengths",
    )
    async def edit_strengths(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        try:
            self.agent._log(f"edit_strengths click user={interaction.user.id}")
            await interaction.response.send_modal(
                ProfileEditStrengthsModal(self.agent, interaction.user)
            )
        except Exception as exc:
            self.agent._log(f"edit_strengths error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ 強み/求める繋がりの編集を開けませんでした。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="話しかけるきっかけ",
        style=discord.ButtonStyle.secondary,
        custom_id="lab_onboarder:edit_starters",
    )
    async def edit_starters(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        try:
            self.agent._log(f"edit_starters click user={interaction.user.id}")
            await interaction.response.send_modal(
                ProfileEditStartersModal(self.agent, interaction.user)
            )
        except Exception as exc:
            self.agent._log(f"edit_starters error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ 話しかけるきっかけの編集を開けませんでした。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="AIで再生成",
        style=discord.ButtonStyle.secondary,
        custom_id="lab_onboarder:edit_ai",
    )
    async def edit_ai(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        try:
            self.agent._log(f"edit_ai click user={interaction.user.id}")
            await interaction.response.send_modal(OnboardModal(self.agent, interaction.user))
        except Exception as exc:
            self.agent._log(f"edit_ai error user={interaction.user.id} err={exc}")
            await self.agent._send_interaction_message(
                interaction,
                "⚠️ AI再生成のモーダルを開けませんでした。",
                ephemeral=True,
            )


class ProfileEditBasicModal(discord.ui.Modal):
    def __init__(self, agent: LabOnboarderAgent, user: discord.User | discord.Member) -> None:
        super().__init__(title="基本情報を編集", custom_id="lab_onboarder:edit_basic_modal")
        self.agent = agent
        record = agent.store.get_profile(user.id)
        self.display_name = discord.ui.TextInput(
            label="表示名",
            required=False,
            max_length=80,
            default=record.display_name if record else None,
        )
        self.handle = discord.ui.TextInput(
            label="@handle（任意）",
            required=False,
            max_length=50,
            default=record.handle if record else None,
        )
        self.one_liner = discord.ui.TextInput(
            label="一言紹介",
            required=False,
            max_length=120,
            default=record.one_liner if record else None,
        )
        self.archetype = discord.ui.TextInput(
            label="アーキタイプ（Builder/Researcher/...）",
            required=False,
            max_length=20,
            default=record.archetype if record else None,
        )
        self.add_item(self.display_name)
        self.add_item(self.handle)
        self.add_item(self.one_liner)
        self.add_item(self.archetype)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.agent._handle_manual_edit(
            interaction,
            display_name=self.display_name.value,
            handle=self.handle.value,
            one_liner=self.one_liner.value,
            archetype=self.archetype.value,
        )


class ProfileEditLinksModal(discord.ui.Modal):
    def __init__(self, agent: LabOnboarderAgent, user: discord.User | discord.Member) -> None:
        super().__init__(title="リンクを編集", custom_id="lab_onboarder:edit_links_modal")
        self.agent = agent
        record = agent.store.get_profile(user.id)
        self.profile_url = discord.ui.TextInput(
            label="プロフィールURL（X以外）",
            required=False,
            max_length=200,
            default=record.profile_url if record else None,
        )
        self.x_profile_url = discord.ui.TextInput(
            label="XプロフィールURL",
            required=False,
            max_length=200,
            default=record.x_profile_url if record else None,
        )
        self.add_item(self.profile_url)
        self.add_item(self.x_profile_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.agent._handle_manual_edit(
            interaction,
            profile_url=self.profile_url.value,
            x_profile_url=self.x_profile_url.value,
        )


class ProfileEditTopicsModal(discord.ui.Modal):
    def __init__(self, agent: LabOnboarderAgent, user: discord.User | discord.Member) -> None:
        super().__init__(title="トピック/ツールを編集", custom_id="lab_onboarder:edit_topics_modal")
        self.agent = agent
        record = agent.store.get_profile(user.id)
        topics_default = _truncate_default("\n".join(record.topics), 600) if record else None
        tools_default = _truncate_default("\n".join(record.tools), 400) if record else None
        channels_default = (
            _truncate_default("\n".join(record.recommended_channels), 200) if record else None
        )
        self.topics = discord.ui.TextInput(
            label="Topics（1行1項目 or カンマ区切り）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=600,
            default=topics_default,
        )
        self.tools = discord.ui.TextInput(
            label="Tools（1行1項目 or カンマ区切り）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=400,
            default=tools_default,
        )
        self.channels = discord.ui.TextInput(
            label="おすすめチャンネル（任意）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=200,
            default=channels_default,
        )
        self.add_item(self.topics)
        self.add_item(self.tools)
        self.add_item(self.channels)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.agent._handle_manual_edit(
            interaction,
            topics=self.topics.value,
            tools=self.tools.value,
            recommended_channels=self.channels.value,
        )


class ProfileEditStrengthsModal(discord.ui.Modal):
    def __init__(self, agent: LabOnboarderAgent, user: discord.User | discord.Member) -> None:
        super().__init__(title="強み/求める繋がりを編集", custom_id="lab_onboarder:edit_strengths_modal")
        self.agent = agent
        record = agent.store.get_profile(user.id)
        strengths_default = _truncate_default("\n".join(record.strengths), 400) if record else None
        looking_default = _truncate_default("\n".join(record.looking_for), 400) if record else None
        self.strengths = discord.ui.TextInput(
            label="強み（1行1項目 or カンマ区切り）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=400,
            default=strengths_default,
        )
        self.looking_for = discord.ui.TextInput(
            label="求める繋がり（1行1項目 or カンマ区切り）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=400,
            default=looking_default,
        )
        self.add_item(self.strengths)
        self.add_item(self.looking_for)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.agent._handle_manual_edit(
            interaction,
            strengths=self.strengths.value,
            looking_for=self.looking_for.value,
        )


class ProfileEditStartersModal(discord.ui.Modal):
    def __init__(self, agent: LabOnboarderAgent, user: discord.User | discord.Member) -> None:
        super().__init__(title="話しかけるきっかけを編集", custom_id="lab_onboarder:edit_starters_modal")
        self.agent = agent
        record = agent.store.get_profile(user.id)
        starters_default = (
            _truncate_default("\n".join(record.conversation_starters), 600) if record else None
        )
        self.starters = discord.ui.TextInput(
            label="話しかけるきっかけ（1行1項目）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=600,
            default=starters_default,
        )
        self.add_item(self.starters)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.agent._handle_manual_edit(
            interaction,
            conversation_starters=self.starters.value,
        )


def _build_chatgpt_prefill_url() -> str:
    prompt = quote(AI_HELP_PROMPT, safe="")
    url = f"{CHATGPT_PREFILL_BASE}{prompt}"
    if len(url) > 512:
        return "https://chatgpt.com/"
    return url


def _is_clear_token(value: str) -> bool:
    token = value.strip().lower()
    return token in {"-", "なし", "none", "clear", "空", "削除"}


def _merge_text_field(value: Optional[str], existing: Optional[str]) -> Optional[str]:
    if value is None:
        return existing
    text = value.strip()
    if not text:
        return existing
    if _is_clear_token(text):
        return None
    return text


def _merge_archetype(value: Optional[str], existing: Optional[str]) -> Optional[str]:
    if value is None:
        return existing
    text = value.strip()
    if not text:
        return existing
    if _is_clear_token(text):
        return None
    for arche in ARCHETYPES:
        if arche.lower() == text.lower():
            return arche
    return None


def _parse_list_text(value: str, *, strip_hash: bool) -> List[str]:
    tokens: List[str] = []
    cleaned = value.replace("、", ",")
    for line in cleaned.splitlines():
        parts = [part.strip() for part in line.split(",")]
        for part in parts:
            if not part:
                continue
            if strip_hash:
                part = part.lstrip("#").strip()
            if part:
                tokens.append(part)
    return tokens


def _merge_list_field(
    value: Optional[str],
    existing: List[str],
    *,
    strip_hash: bool,
) -> List[str]:
    if value is None:
        return existing
    text = value.strip()
    if not text:
        return existing
    if _is_clear_token(text):
        return []
    parsed = _parse_list_text(text, strip_hash=strip_hash)
    return parsed or existing


def _truncate_default(value: Optional[str], max_length: int) -> Optional[str]:
    if not value:
        return None
    if len(value) <= max_length:
        return value
    return value[:max_length]


def _split_log_chunks(text: str, limit: int) -> List[str]:
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


def _extract_channel_snippet(content: str, channel_id: int) -> str:
    if not content:
        return ""
    token = f"/{channel_id}/"
    for line in content.splitlines():
        if token not in line:
            continue
        cleaned = re.sub(r"https://discord\\.com/channels/\\d+/\\d+/\\d+", "", line)
        cleaned = cleaned.strip(" -•\t")
        if cleaned:
            return cleaned[:120]
    return ""


def _extract_channel_ids(text: str) -> List[int]:
    ids: List[int] = []
    seen = set()
    for match in CHANNEL_LINK_PATTERN.finditer(text or ""):
        channel_id = match.group(1)
        if channel_id in seen:
            continue
        seen.add(channel_id)
        ids.append(int(channel_id))
    return ids


def _build_x_llm_prompt(user: Dict[str, Any], tweets: List[Dict[str, Any]]) -> str:
    user_metrics = user.get("public_metrics") or {}
    tweet_texts = []
    for tweet in tweets[:X_TWEET_MAX_RESULTS]:
        text = str(tweet.get("text") or "").strip()
        if not text:
            continue
        tweet_texts.append(text)

    payload = {
        "name": user.get("name"),
        "username": user.get("username"),
        "description": user.get("description"),
        "location": user.get("location"),
        "url": user.get("url"),
        "verified": user.get("verified"),
        "created_at": user.get("created_at"),
        "metrics": user_metrics,
        "tweets": tweet_texts,
    }
    data_json = json.dumps(payload, ensure_ascii=False, indent=2)
    schema_json = json.dumps(PROFILE_SCHEMA, ensure_ascii=False, indent=2)

    return (
        f"{PROFILE_QUERY}\n\n"
        "以下はX APIで取得した公開情報です。\n"
        "この情報だけを根拠にプロフィールカードを作ってください。\n\n"
        "## JSON Schema\n"
        f"{schema_json}\n\n"
        "## X API Data\n"
        f"{data_json}\n"
    )


def _heuristic_card_from_x(user: Dict[str, Any], tweets: List[Dict[str, Any]]) -> Dict[str, Any]:
    description = str(user.get("description") or "").strip()
    handle = str(user.get("username") or "").strip().lstrip("@")
    display = str(user.get("name") or handle or "Member")

    topics = _extract_hashtags(tweets)
    if not topics and description:
        topics = _extract_keywords(description, limit=6)
    if not topics:
        topics = ["AI"]

    starters = [
        "最近取り組んでいることを教えてください！",
        "今ハマっているAIツールはありますか？",
        "今後やってみたいことは何ですか？",
    ]

    return {
        "displayName": display,
        "handle": handle,
        "oneLiner": description[:200] if description else "Xプロフィールから作成",
        "archetype": "Curator",
        "topics": topics,
        "tools": [],
        "strengths": [],
        "cautions": [],
        "lookingFor": [],
        "conversationStarters": starters,
        "recommendedChannels": ["random", "tools", "topics"],
    }


def _extract_hashtags(tweets: List[Dict[str, Any]]) -> List[str]:
    tags: List[str] = []
    seen = set()
    for tweet in tweets:
        text = str(tweet.get("text") or "")
        for tag in re.findall(r"#(\S+)", text):
            cleaned = tag.strip().strip("#").strip()
            if not cleaned:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            tags.append(cleaned)
            if len(tags) >= 8:
                return tags
    return tags


def _extract_keywords(text: str, *, limit: int = 6) -> List[str]:
    tokens = re.split(r"[\\s,、。/|]+", text)
    words: List[str] = []
    seen = set()
    for token in tokens:
        cleaned = token.strip().strip("#@").strip()
        if len(cleaned) < 3:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        words.append(cleaned)
        if len(words) >= limit:
            break
    return words


def _normalize_url(url: str) -> Optional[str]:
    if not url:
        return None
    cleaned = url.strip().strip("<>").strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        cleaned = f"https://{cleaned}"
        parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return cleaned


def _is_cache_fresh(fetched_at: Optional[str], ttl_minutes: int) -> bool:
    if not fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except Exception:
        return False
    age = datetime.now(timezone.utc) - fetched
    return age.total_seconds() <= (ttl_minutes * 60)


def _maybe_get_cached_llm_card(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Placeholder hook if we later cache LLM outputs in payload
    card = payload.get("card")
    if isinstance(card, dict):
        return card
    return None


class XApiRateLimitError(RuntimeError):
    pass


class XApiPermissionError(RuntimeError):
    pass

def _extract_exa_status_error(data: Any, url: str) -> tuple[Optional[str], Optional[str]]:
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if isinstance(statuses, list):
        for status in statuses:
            if status.get("status") != "error":
                continue
            status_id = status.get("id")
            if status_id == url or (not status_id and len(statuses) == 1):
                tag = status.get("error", {}).get("tag", "UNKNOWN")
                code = status.get("error", {}).get("httpStatusCode", "n/a")
                return tag, f"Exa crawl error: {tag} ({code})."
    return None, None


def _extract_exa_any_error(data: Any) -> tuple[Optional[str], Optional[str]]:
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if isinstance(statuses, list):
        for status in statuses:
            if status.get("status") != "error":
                continue
            tag = status.get("error", {}).get("tag", "UNKNOWN")
            code = status.get("error", {}).get("httpStatusCode", "n/a")
            return tag, f"Exa crawl error: {tag} ({code})."
    return None, None


def _extract_summary_from_contents(data: Any) -> tuple[Optional[Any], Optional[str]]:
    if not isinstance(data, dict):
        return None, None
    results = data.get("results")
    if not isinstance(results, list):
        return None, None

    status_map: Dict[str, Dict[str, Any]] = {}
    statuses = data.get("statuses")
    if isinstance(statuses, list):
        for status in statuses:
            status_id = status.get("id")
            if isinstance(status_id, str):
                status_map[status_id] = status

    for result in results:
        if not isinstance(result, dict):
            continue
        summary = result.get("summary")
        if not summary:
            continue
        result_id = result.get("id") or result.get("url")
        status = status_map.get(result_id)
        if status and status.get("status") == "error":
            continue
        raw_summary = summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False)
        return summary, raw_summary

    return None, None


def _extract_search_urls(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    urls: List[str] = []
    seen = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        url = result.get("url") or result.get("id")
        if not isinstance(url, str):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _is_x_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host and parsed.path:
        host = parsed.path.split("/")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host.endswith("x.com") or host.endswith("twitter.com")


def _strip_scheme(url: str) -> str:
    if url.startswith("http://"):
        return url[len("http://") :]
    if url.startswith("https://"):
        return url[len("https://") :]
    return url


def _x_fallback_urls(url: str) -> List[str]:
    stripped = _strip_scheme(url)
    return [
        f"https://r.jina.ai/http://{stripped}",
    ]


def _extract_x_handle(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    path = parsed.path or ""
    if not path and parsed.netloc:
        path = parsed.netloc
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    handle = parts[0].lstrip("@")
    if handle.lower() in {"x.com", "twitter.com"} and len(parts) > 1:
        handle = parts[1].lstrip("@")
    if not handle:
        return None
    return handle[:50]


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _jaccard(a: List[str], b: List[str]) -> float:
    set_a = {str(item).lower() for item in (a or [])}
    set_b = {str(item).lower() for item in (b or [])}
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)
