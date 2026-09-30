from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from .config import InviteRoleServiceConfig
from .entry import (
    MESSAGE_PASSPHRASE_EMPTY,
    MESSAGE_SUPPORT_FAILED,
    MESSAGE_SUPPORT_SENT,
    MESSAGE_WRONG_GUILD,
    EntryBackendClient,
    course_claim_message,
    normalize_passphrase,
    status_message,
    support_request_message,
)
from .entry_panel import EntryPanelView
from .join import plan_join_role_ids
from .storage import CsvLogStore, JsonStateFile, MemberJoinRecord


class WelcomeDeliveryError(RuntimeError):
    def __init__(self, message: str, *, retry_safe: bool = False):
        super().__init__(message)
        self.retry_safe = retry_safe


class InviteRoleService(discord.Client):
    def __init__(self, config: InviteRoleServiceConfig):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.config = config
        self.logger = self._build_logger()
        self.member_log = CsvLogStore(config.member_log_path)
        self.heartbeat_state = JsonStateFile(config.heartbeat_path)
        self.baseline_state = JsonStateFile(config.baseline_path)
        self.welcome_state = (
            JsonStateFile(config.welcome.state_path)
            if config.welcome is not None
            else None
        )
        self.entry_backend = (
            EntryBackendClient(config.entry) if config.entry is not None else None
        )
        self._invite_uses: dict[str, int] = {}
        self._guild: discord.Guild | None = None
        self._lock = asyncio.Lock()
        self._welcome_lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._baseline_task: asyncio.Task[None] | None = None
        self._welcome_recovery_task: asyncio.Task[None] | None = None
        self._welcome_followup_task: asyncio.Task[None] | None = None

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"invite_role_service.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        self.config.service_log_path.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = logging.FileHandler(
            self.config.service_log_path, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        return logger

    async def setup_hook(self) -> None:
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="invite-role-heartbeat"
        )
        self._baseline_task = asyncio.create_task(
            self._baseline_refresh_loop(), name="invite-role-baseline"
        )
        entry = self.config.entry
        if entry is not None:
            # Persistent view: same custom_ids after restart, so the posted
            # panel keeps working. message_id is optional.
            self.add_view(
                EntryPanelView(entry, self), message_id=entry.panel_message_id
            )
            self.logger.info(
                "registered entry panel persistent view message=%s",
                entry.panel_message_id or "any",
            )

    async def close(self) -> None:
        tasks = [
            task
            for task in (
                self._heartbeat_task,
                self._baseline_task,
                self._welcome_recovery_task,
                self._welcome_followup_task,
            )
            if task is not None
        ]
        for task in tasks:
            if task:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await super().close()

    async def on_ready(self) -> None:
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            guild = await self.fetch_guild(self.config.guild_id)
        self._guild = guild
        await self._refresh_invites_baseline(reason="startup")
        for key in self.config.ignored_legacy_keys:
            self.logger.warning(
                "config key '%s' is no longer supported and is ignored", key
            )
        if self.config.entry is not None:
            entry = self.config.entry
            self.logger.info(
                "entry ready channel=%s panel_message=%s support_channel=%s backend=%s token_configured=%s token_env=%s",
                entry.channel_id or "none",
                entry.panel_message_id or "none",
                entry.support_channel_id or "none",
                entry.backend_base_url,
                entry.backend_token() is not None,
                entry.backend_token_env,
            )
        if self.config.welcome is not None and self.config.welcome.enabled:
            await self._validate_welcome_setup(guild)
            if (
                self._welcome_recovery_task is None
                or self._welcome_recovery_task.done()
            ):
                self._welcome_recovery_task = asyncio.create_task(
                    self._recover_pending_welcomes(guild),
                    name="invite-role-welcome-recovery",
                )
            followup = self.config.welcome.followup
            if (
                followup is not None
                and followup.enabled
                and (
                    self._welcome_followup_task is None
                    or self._welcome_followup_task.done()
                )
            ):
                self._welcome_followup_task = asyncio.create_task(
                    self._welcome_followup_loop(guild),
                    name="invite-role-welcome-followup",
                )
                self.logger.info(
                    "welcome followup ready delay_seconds=%s check_interval_seconds=%s archive_ok=%s",
                    followup.delay_seconds,
                    followup.check_interval_seconds,
                    followup.discrawl_db_path.is_file(),
                )
        await self._write_heartbeat(status="ready")
        await self._log(
            f"ready guild={guild.id} general_role={self.config.general_role_id} review_role={self.config.review_role_id}"
        )

    async def on_resumed(self) -> None:
        await self._refresh_invites_baseline(reason="resumed")
        await self._write_heartbeat(status="resumed")

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.config.guild_id:
            return

        welcome_context: tuple[str, str] | None = None
        async with self._lock:
            used_code, debug_detail = await self._detect_used_invite_code(member.guild)
            if self.config.is_excluded_code(used_code):
                role_names = "none(excluded)"
                invite_type = "excluded"
                detection_method = "excluded_invite_code"
                self.member_log.append(
                    MemberJoinRecord.create(
                        discord_id=member.id,
                        username=str(member),
                        invite_code=used_code or "UNKNOWN",
                        phase="excluded",
                        role_assigned=role_names,
                        detection_method=detection_method,
                    )
                )
                await self._refresh_invites_baseline(reason="after_excluded_join")
                await self._write_heartbeat(status="processed_excluded_join")
                await self._log(
                    f"member={member.id} invite={used_code} type={invite_type} roles={role_names} detail={debug_detail}"
                )
                return

            phase = self.config.phase_for_code(used_code)
            if phase:
                invite_type = f"invited:{phase}"
            elif used_code:
                invite_type = "general"
            else:
                invite_type = "unknown"

            detection_method = "uses_diff"
            if "vanished=" in debug_detail and used_code:
                detection_method = "vanished"
            elif used_code is None:
                detection_method = "detection_failed"

            # The general role (agi-lab) is no longer granted on join; agi-backend
            # decides it. Only the review role is added when detection failed.
            roles_to_add: list[discord.Role] = []
            for role_id in plan_join_role_ids(
                used_code=used_code, review_role_id=self.config.review_role_id
            ):
                role = member.guild.get_role(role_id)
                if role is None:
                    await self._log(f"join role missing: {role_id}")
                    continue
                roles_to_add.append(role)

            assigned_roles: list[discord.Role] = []
            if roles_to_add:
                try:
                    await member.add_roles(
                        *roles_to_add, reason=f"invite-role-service: {invite_type}"
                    )
                    assigned_roles = roles_to_add
                except Exception as exc:
                    self.logger.exception("failed assigning roles to %s", member.id)
                    await self._log(
                        f"role assignment failed member={member.id} error={type(exc).__name__}: {exc}"
                    )

            role_names = ", ".join(role.name for role in assigned_roles) or "none"
            self.member_log.append(
                MemberJoinRecord.create(
                    discord_id=member.id,
                    username=str(member),
                    invite_code=used_code or "UNKNOWN",
                    phase=phase or "",
                    role_assigned=role_names,
                    detection_method=detection_method,
                )
            )
            await self._refresh_invites_baseline(reason="after_join")
            await self._write_heartbeat(status="processed_join")
            await self._log(
                f"member={member.id} invite={used_code or 'UNKNOWN'} type={invite_type} roles={role_names} detail={debug_detail}"
            )
            welcome_context = (invite_type, role_names)

        if welcome_context is not None:
            invite_type, role_names = welcome_context
            await self._send_welcome_if_enabled(
                member=member, invite_type=invite_type, role_names=role_names
            )

    def _entry_interaction_allowed(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        return guild is not None and guild.id == self.config.guild_id

    async def _defer_ephemeral(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

    async def handle_entry_course_claim(
        self, interaction: discord.Interaction, passphrase: str
    ) -> None:
        """講座に参加: relay the passphrase to agi-backend. Never log the passphrase."""
        if not self._entry_interaction_allowed(interaction):
            await self._respond_ephemeral(interaction, MESSAGE_WRONG_GUILD)
            return
        passphrase = normalize_passphrase(passphrase)
        if not passphrase:
            await self._respond_ephemeral(interaction, MESSAGE_PASSPHRASE_EMPTY)
            return
        assert self.entry_backend is not None
        await self._defer_ephemeral(interaction)
        result = await self.entry_backend.claim_course(
            discord_user_id=interaction.user.id, passphrase=passphrase
        )
        await self._respond_ephemeral(interaction, course_claim_message(result))
        await self._log(
            f"entry course_claim member={interaction.user.id} {result.log_label} "
            f"already_claimed={bool(result.data.get('already_claimed'))}"
        )

    async def handle_entry_status(self, interaction: discord.Interaction) -> None:
        """参加状態を確認: ephemeral status from agi-backend."""
        if not self._entry_interaction_allowed(interaction):
            await self._respond_ephemeral(interaction, MESSAGE_WRONG_GUILD)
            return
        assert self.entry_backend is not None
        await self._defer_ephemeral(interaction)
        result = await self.entry_backend.get_status(
            discord_user_id=interaction.user.id
        )
        await self._respond_ephemeral(interaction, status_message(result))
        line = f"entry status member={interaction.user.id} {result.log_label}"
        if result.success:
            self.logger.info(line)
        else:
            await self._log(line)

    async def handle_entry_support(
        self, interaction: discord.Interaction, text: str
    ) -> None:
        """連携できない時: forward to the ops channel. The text is not logged."""
        if not self._entry_interaction_allowed(interaction):
            await self._respond_ephemeral(interaction, MESSAGE_WRONG_GUILD)
            return
        entry = self.config.entry
        channel_id = entry.support_channel_id if entry is not None else None
        user = interaction.user
        if channel_id is None:
            await self._respond_ephemeral(interaction, MESSAGE_SUPPORT_FAILED)
            await self._log(
                f"entry support dropped member={user.id} reason=support_channel_not_configured"
            )
            return
        await self._defer_ephemeral(interaction)
        try:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise RuntimeError("support channel is not a text channel")
            await channel.send(
                support_request_message(
                    user_id=user.id,
                    user_mention=user.mention,
                    username=str(user),
                    text=text,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            self.logger.warning(
                "entry support forward failed member=%s error=%s",
                user.id,
                type(exc).__name__,
            )
            await self._respond_ephemeral(interaction, MESSAGE_SUPPORT_FAILED)
            await self._log(
                f"entry support failed member={user.id} error={type(exc).__name__}"
            )
            return
        await self._respond_ephemeral(interaction, MESSAGE_SUPPORT_SENT)
        await self._log(f"entry support forwarded member={user.id} length={len(text)}")

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._write_heartbeat(status="running")
            await asyncio.sleep(self.config.heartbeat_interval_seconds)

    async def _baseline_refresh_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            if self._guild is not None:
                await self._refresh_invites_baseline(reason="periodic")
            await asyncio.sleep(self.config.baseline_refresh_seconds)

    async def _refresh_invites_baseline(self, *, reason: str) -> None:
        guild = self._guild
        if guild is None:
            return
        try:
            invites = await guild.invites()
            self._invite_uses = {invite.code: (invite.uses or 0) for invite in invites}
            self.baseline_state.write(
                {
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                    "reason": reason,
                    "guildId": self.config.guild_id,
                    "inviteUses": self._invite_uses,
                }
            )
        except Exception as exc:
            self.logger.warning("baseline refresh failed: %s", exc)
            await self._log(
                f"baseline refresh failed reason={reason} error={type(exc).__name__}: {exc}"
            )

    async def _detect_used_invite_code(
        self, guild: discord.Guild
    ) -> tuple[str | None, str]:
        previous = dict(self._invite_uses)
        retry_delays = [0.0, 0.8, 1.6]
        last_debug = "changes=none"

        for attempt, delay in enumerate(retry_delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                invites = await guild.invites()
            except discord.Forbidden:
                return None, "forbidden"
            except Exception as exc:
                return None, f"error={type(exc).__name__}:{exc}"

            current = {invite.code: (invite.uses or 0) for invite in invites}
            changes = []
            for code, uses in current.items():
                previous_uses = previous.get(code, 0)
                if uses > previous_uses:
                    changes.append((code, previous_uses, uses))
            vanished = [code for code in previous if code not in current]

            parts = []
            parts.append(
                "changes="
                + (
                    ", ".join(
                        f"{code}:{before}->{after}" for code, before, after in changes
                    )
                    if changes
                    else "none"
                )
            )
            if vanished:
                parts.append("vanished=" + ",".join(vanished))
            if attempt:
                parts.append(f"retry={attempt}")
            last_debug = ";".join(parts)

            if len(changes) == 1 and changes[0][2] - changes[0][1] == 1:
                return changes[0][0], last_debug
            if not changes and len(vanished) == 1:
                return vanished[0], last_debug

        return None, last_debug

    async def _resolve_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if self.user is None:
            return None
        member = guild.get_member(self.user.id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(self.user.id)
        except Exception:
            return None

    def _build_welcome_agent_prompt(
        self,
        *,
        member: discord.Member,
        invite_type: str,
        role_names: str,
        destination_channel_id: int | None = None,
    ) -> str:
        cfg = self.config.welcome
        assert cfg is not None
        destination_channel_id = destination_channel_id or self._welcome_channel_id()
        intro_line = (
            f"- 自己紹介チャンネル: <#{cfg.intro_channel_id}>"
            if cfg.intro_channel_id
            else "- 自己紹介チャンネル: 未指定"
        )
        guide_line = (
            f"- 全体案内チャンネル: <#{cfg.guide_channel_id}>"
            if cfg.guide_channel_id
            else "- 全体案内チャンネル: 未指定"
        )
        entry_lines = ""
        entry = self.config.entry
        if entry is not None and entry.channel_id:
            entry_lines = (
                f"- 入口チャンネル: <#{entry.channel_id}>\n"
                "- 入室しただけでは会員向けの部屋はまだ見えない。"
                "会員の方は入口の「Discord 連携」、講座に申し込んだ方は入口の「講座に参加」で開くことを、1文で自然に添える。\n"
            )
        return (
            "あなたはAGIラボDiscordにいるラボちゃん本人です。\n"
            "新しくAGIラボに入った人へ、本人の歓迎スレッドでメッセージを1つだけ送ります。\n\n"
            "必ず踏む手順:\n"
            "1. まずAGENTS.md/VISION.md/TOOLS.mdの姿勢に従う。\n"
            "2. 利用可能ならDiscord文脈検索で、対象ユーザーの過去投稿、自己紹介チャンネルでの投稿、最近のラボちゃん部屋/自己紹介チャンネルの流れを軽く確認する。\n"
            "   特に直近24時間の公開会話から、開催中・開催直後の講座、制作、盛り上がっている問いを最大2件だけ拾う。\n"
            "3. 対象ユーザーが自己紹介済みだと分かったら、自己紹介を促さない。自己紹介に触れるなら、その内容を踏まえた歓迎や次の導線にする。\n"
            "4. 自己紹介が見つからなければ、未済と断定せず、書きやすい軽い一歩として自己紹介チャンネルを案内する。\n"
            "5. 見つかった事実だけを使い、見つからなければ一般的だが相手に向けた歓迎にする。\n\n"
            "出力ルール:\n"
            "- 最終出力はそのままDiscordに投稿される本文だけ。\n"
            "- 3〜6文程度。箇条書きにはせず、短い2段落までにする。\n"
            "- 必ず対象者をDiscord mentionで呼ぶ。\n"
            "- 対象者以外のユーザー、ロール、@everyone、@hereをmentionしない。\n"
            "- 自己紹介チャンネルへの誘導は、未投稿だと確認できた時か、新規参加者に明らかに有益な時だけ。既に投稿済みなら絶対に促さない。\n"
            "- 過去文脈がなくても、全体案内と自己紹介の2チャンネルは必ず実リンクで案内する。このスレッドで質問できることも自然に含める。\n"
            "- 直近24時間に公開のホットな話題があれば、初参加者にも意味が通る説明を1つだけ添える。内輪話、個人事情、古い話題は使わない。\n"
            "- 本人の公開発言が見つかれば、一般的なホットトピックより本人の言葉を優先する。見つからない情報で個人化しない。\n"
            "- 毎回同じ順番・同じ締めにしない。『今日のラボから入る』『静かな散歩として案内する』『軽い問いから入る』など、その場に合う構成を選ぶ。\n"
            "- 過去発言や関心が見えた場合は、その人に合いそうな場所を1〜2個だけ提案する。\n"
            "- ツール名、内部手順、検索ログ、ローカルパス、設定値、ID一覧、配送状態は書かない。\n"
            "- 「低頻度」「テスト」「自動送信」「bot処理」のような運用都合は書かない。\n"
            "- 声や語尾はここで作り直さず、AGENTS.md/IDENTITY.md/SOUL.mdとcommunity-welcomeの現在の姿勢に従う。\n\n"
            f"対象者: <@{member.id}>\n"
            f"{guide_line}\n"
            f"{intro_line}\n"
            f"- 投稿先の歓迎スレッド: <#{destination_channel_id}>\n"
            f"{entry_lines}"
        )

    def _welcome_thread_name(self, member: discord.Member) -> str:
        display_name = getattr(member, "display_name", None) or str(member)
        display_name = "".join(
            character
            for character in display_name
            if not unicodedata.category(character).startswith("C")
        )
        display_name = " ".join(display_name.split()).strip() or f"member-{member.id}"
        prefix = "👋-"
        suffix = "センパイ、ようこそ！"
        max_name_length = 100 - len(prefix) - len(suffix)
        return f"{prefix}{display_name[:max_name_length]}{suffix}"

    def _welcome_channel_id(self) -> int:
        """Welcome threads go to the entry channel when it is configured."""
        entry = self.config.entry
        if entry is not None and entry.channel_id:
            return entry.channel_id
        cfg = self.config.welcome
        assert cfg is not None
        return cfg.channel_id

    async def _welcome_parent_channel(self) -> discord.TextChannel:
        channel_id = self._welcome_channel_id()
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"welcome channel is not a text channel: {channel_id}")
        return channel

    async def _create_welcome_destination(
        self,
        *,
        member: discord.Member,
        invite_type: str = "",
        role_names: str = "",
    ) -> int:
        cfg = self.config.welcome
        assert cfg is not None
        pending = self._pending_welcome_for(member.id)
        pending_destination = self._pending_destination_id(pending)
        if pending_destination is not None:
            return pending_destination
        if not cfg.threaded:
            self._mark_welcome_pending(
                member=member,
                invite_type=invite_type,
                role_names=role_names,
                destination_channel_id=self._welcome_channel_id(),
                status="destination_created",
            )
            return self._welcome_channel_id()

        channel = await self._welcome_parent_channel()
        starter: discord.Message | Any | None = None
        starter_message_id = self._pending_starter_message_id(pending)
        if starter_message_id is not None:
            starter = await channel.fetch_message(starter_message_id)
            existing_thread = getattr(starter, "thread", None)
            if isinstance(existing_thread, discord.Thread):
                self._mark_welcome_pending(
                    member=member,
                    invite_type=invite_type,
                    role_names=role_names,
                    destination_channel_id=existing_thread.id,
                    starter_message_id=starter_message_id,
                    status="destination_created",
                )
                return existing_thread.id

        if starter is None:
            starter = await channel.send(
                f"👋 {member.mention}センパイ、AGIラボへようこそ！",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[member],
                    replied_user=False,
                ),
            )
            starter_message_id = starter.id
            self._mark_welcome_pending(
                member=member,
                invite_type=invite_type,
                role_names=role_names,
                starter_message_id=starter_message_id,
                status="starter_created",
            )

        thread = await starter.create_thread(
            name=self._welcome_thread_name(member),
            auto_archive_duration=cfg.thread_auto_archive_minutes,
            reason="新規メンバーのオンボーディング",
        )
        self._mark_welcome_pending(
            member=member,
            invite_type=invite_type,
            role_names=role_names,
            destination_channel_id=thread.id,
            starter_message_id=starter_message_id,
            status="destination_created",
        )
        return thread.id

    def _read_welcome_state(self) -> dict[str, Any]:
        if self.welcome_state is None:
            return {"welcomed": {}, "pending": {}, "followups": {}}
        state = self.welcome_state.read(
            {"welcomed": {}, "pending": {}, "followups": {}}
        )
        if not isinstance(state, dict):
            return {"welcomed": {}, "pending": {}, "followups": {}}
        if not isinstance(state.get("welcomed"), dict):
            state["welcomed"] = {}
        if not isinstance(state.get("pending"), dict):
            state["pending"] = {}
        if not isinstance(state.get("followups"), dict):
            state["followups"] = {}
        return state

    def _welcome_record_for(self, user_id: int) -> dict[str, Any] | None:
        if self.welcome_state is None:
            return None
        state = self._read_welcome_state()
        welcomed = state["welcomed"]
        record = welcomed.get(str(user_id))
        return record if isinstance(record, dict) else None

    def _pending_welcome_for(self, user_id: int) -> dict[str, Any] | None:
        if self.welcome_state is None:
            return None
        pending = self._read_welcome_state()["pending"]
        record = pending.get(str(user_id))
        return record if isinstance(record, dict) else None

    def _followup_record_for(self, user_id: int) -> dict[str, Any] | None:
        if self.welcome_state is None:
            return None
        record = self._read_welcome_state()["followups"].get(str(user_id))
        return record if isinstance(record, dict) else None

    def _mark_followup(
        self,
        *,
        member_id: int,
        status: str,
        thread_id: int | None = None,
        attempts: int | None = None,
        error_type: str | None = None,
    ) -> None:
        if self.welcome_state is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        state = self._read_welcome_state()
        followups = state["followups"]
        record = followups.get(str(member_id))
        if not isinstance(record, dict):
            record = {"createdAt": now, "attempts": 0}
        record.update({"updatedAt": now, "status": status})
        if thread_id is not None:
            record["threadId"] = thread_id
        if attempts is not None:
            record["attempts"] = attempts
        if error_type is not None:
            record["lastErrorType"] = error_type
        else:
            record.pop("lastErrorType", None)
        if status == "sent":
            record["sentAt"] = now
        elif status.startswith("skipped_"):
            record["skippedAt"] = now
        followups[str(member_id)] = record
        self.welcome_state.write(state)

    @staticmethod
    def _pending_destination_id(record: dict[str, Any] | None) -> int | None:
        raw = record.get("threadId") if record else None
        if isinstance(raw, int) and raw > 0:
            return raw
        return None

    @staticmethod
    def _pending_starter_message_id(record: dict[str, Any] | None) -> int | None:
        raw = record.get("starterMessageId") if record else None
        if isinstance(raw, int) and raw > 0:
            return raw
        return None

    def _mark_welcome_pending(
        self,
        *,
        member: discord.Member,
        invite_type: str,
        role_names: str,
        status: str,
        destination_channel_id: int | None = None,
        starter_message_id: int | None = None,
        attempts: int | None = None,
        error_type: str | None = None,
    ) -> None:
        if self.welcome_state is None:
            return
        cfg = self.config.welcome
        assert cfg is not None
        state = self._read_welcome_state()
        pending = state["pending"]
        record = pending.get(str(member.id))
        if not isinstance(record, dict):
            record = {
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "attempts": 0,
            }
        record.update(
            {
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "username": str(member),
                "inviteType": invite_type,
                "roles": role_names,
                "channelId": self._welcome_channel_id(),
                "status": status,
            }
        )
        if destination_channel_id is not None:
            record["threadId"] = destination_channel_id
        if starter_message_id is not None:
            record["starterMessageId"] = starter_message_id
        if attempts is not None:
            record["attempts"] = attempts
        if error_type is not None:
            record["lastErrorType"] = error_type
        else:
            record.pop("lastErrorType", None)
        pending[str(member.id)] = record
        self.welcome_state.write(state)

    def _mark_welcomed(
        self,
        *,
        member: discord.Member,
        invite_type: str,
        role_names: str,
        destination_channel_id: int,
    ) -> None:
        if self.welcome_state is None:
            return
        state = self._read_welcome_state()
        welcomed = state["welcomed"]
        welcomed[str(member.id)] = {
            "welcomedAt": datetime.now(timezone.utc).isoformat(),
            "username": str(member),
            "inviteType": invite_type,
            "roles": role_names,
            "channelId": (self._welcome_channel_id() if self.config.welcome else None),
            "threadId": destination_channel_id,
        }
        state["pending"].pop(str(member.id), None)
        self.welcome_state.write(state)

    async def _send_welcome_if_enabled(
        self, *, member: discord.Member, invite_type: str, role_names: str
    ) -> None:
        cfg = self.config.welcome
        if cfg is None or not cfg.enabled:
            return
        if cfg.skip_bots and member.bot:
            await self._log(f"welcome skipped member={member.id} reason=bot")
            return
        async with self._welcome_lock:
            if self._welcome_record_for(member.id) is not None:
                await self._log(
                    f"welcome skipped member={member.id} reason=already_sent"
                )
                return

            pending = self._pending_welcome_for(member.id)
            pending_status = str((pending or {}).get("status") or "")
            if pending_status in {"delivery_started", "delivery_uncertain"}:
                await self._log(
                    f"welcome skipped member={member.id} reason=delivery_uncertain"
                )
                return

            destination_channel_id: int | None = None
            for destination_attempt in range(1, cfg.max_attempts + 1):
                try:
                    destination_channel_id = await self._create_welcome_destination(
                        member=member,
                        invite_type=invite_type,
                        role_names=role_names,
                    )
                    break
                except Exception as exc:
                    self.logger.exception(
                        "welcome destination failed for %s", member.id
                    )
                    await self._log(
                        f"welcome destination failed member={member.id} attempt={destination_attempt} error={type(exc).__name__}: {exc}"
                    )
                    if destination_attempt < cfg.max_attempts:
                        await asyncio.sleep(cfg.retry_delay_seconds)
            if destination_channel_id is None:
                return

            prompt = self._build_welcome_agent_prompt(
                member=member,
                invite_type=invite_type,
                role_names=role_names,
                destination_channel_id=destination_channel_id,
            )
            pending = self._pending_welcome_for(member.id) or {}
            attempts = int(pending.get("attempts") or 0)
            if attempts >= cfg.max_attempts:
                created_at = pending.get("createdAt")
                since = None
                if isinstance(created_at, str):
                    try:
                        since = datetime.fromisoformat(created_at)
                    except ValueError:
                        since = None
                if await self._welcome_message_exists_since(
                    destination_channel_id=destination_channel_id,
                    member_id=member.id,
                    since=since,
                ):
                    self._mark_welcomed(
                        member=member,
                        invite_type=invite_type,
                        role_names=role_names,
                        destination_channel_id=destination_channel_id,
                    )
                    await self._log(
                        f"welcome reconciled member={member.id} thread={destination_channel_id}"
                    )
                    return
                await self._log(
                    f"welcome skipped member={member.id} reason=attempts_exhausted attempts={attempts}"
                )
                return
            while attempts < cfg.max_attempts:
                attempts += 1
                self._mark_welcome_pending(
                    member=member,
                    invite_type=invite_type,
                    role_names=role_names,
                    destination_channel_id=destination_channel_id,
                    starter_message_id=self._pending_starter_message_id(pending),
                    status="delivery_started",
                    attempts=attempts,
                )
                attempt_started_at = datetime.now(timezone.utc)
                try:
                    await self._run_openclaw_agent_welcome(
                        prompt=prompt,
                        member_id=member.id,
                        destination_channel_id=destination_channel_id,
                    )
                except Exception as exc:
                    if await self._welcome_message_exists_since(
                        destination_channel_id=destination_channel_id,
                        member_id=member.id,
                        since=attempt_started_at,
                    ):
                        self._mark_welcomed(
                            member=member,
                            invite_type=invite_type,
                            role_names=role_names,
                            destination_channel_id=destination_channel_id,
                        )
                        await self._log(
                            f"welcome reconciled member={member.id} thread={destination_channel_id} after_missing_ack"
                        )
                        return
                    retry_safe = (
                        isinstance(exc, WelcomeDeliveryError) and exc.retry_safe
                    )
                    status = "retryable_failure" if retry_safe else "delivery_uncertain"
                    self._mark_welcome_pending(
                        member=member,
                        invite_type=invite_type,
                        role_names=role_names,
                        destination_channel_id=destination_channel_id,
                        starter_message_id=self._pending_starter_message_id(pending),
                        status=status,
                        attempts=attempts,
                        error_type=type(exc).__name__,
                    )
                    self.logger.exception("welcome send failed for %s", member.id)
                    await self._log(
                        f"welcome failed member={member.id} attempt={attempts} retry_safe={retry_safe} error={type(exc).__name__}: {exc}"
                    )
                    if retry_safe and attempts < cfg.max_attempts:
                        await asyncio.sleep(cfg.retry_delay_seconds)
                        pending = self._pending_welcome_for(member.id) or pending
                        continue
                    return

                self._mark_welcomed(
                    member=member,
                    invite_type=invite_type,
                    role_names=role_names,
                    destination_channel_id=destination_channel_id,
                )
                await self._log(
                    f"welcome sent member={member.id} channel={self._welcome_channel_id()} thread={destination_channel_id}"
                )
                return

    async def _welcome_message_exists_since(
        self,
        *,
        destination_channel_id: int,
        member_id: int,
        since: datetime | None,
    ) -> bool:
        """Reconcile a missing delivery ACK against the personal welcome thread."""
        try:
            channel = self.get_channel(destination_channel_id)
            if channel is None:
                channel = await self.fetch_channel(destination_channel_id)
            history = getattr(channel, "history", None)
            if history is None:
                return False
            async for message in history(limit=10, after=since, oldest_first=False):
                author = getattr(message, "author", None)
                content = str(getattr(message, "content", "") or "")
                if bool(getattr(author, "bot", False)) and f"<@{member_id}>" in content:
                    return True
        except Exception:
            self.logger.exception(
                "welcome reconciliation failed for member=%s thread=%s",
                member_id,
                destination_channel_id,
            )
        return False

    async def _run_openclaw_agent_welcome(
        self,
        *,
        prompt: str,
        member_id: int,
        destination_channel_id: int,
        session_scope: str = "welcome",
    ) -> None:
        cfg = self.config.welcome
        assert cfg is not None
        env = os.environ.copy()
        if cfg.node_bin_dir is not None:
            env["PATH"] = f"{cfg.node_bin_dir}:{env.get('PATH', '')}"

        gateway_timeout_seconds = max(cfg.timeout_seconds - 10, 5)
        try:
            proc = await asyncio.create_subprocess_exec(
                cfg.openclaw_bin,
                "agent",
                "--agent",
                cfg.agent_id,
                "--session-key",
                f"agent:{cfg.agent_id}:{session_scope}-{member_id}",
                "--thinking",
                cfg.thinking,
                "--timeout",
                str(gateway_timeout_seconds),
                "--message",
                prompt,
                "--deliver",
                "--reply-channel",
                "discord",
                "--reply-to",
                f"channel:{destination_channel_id}",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cfg.openclaw_cwd),
                env=env,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise WelcomeDeliveryError(
                f"OpenClaw process could not start: {type(exc).__name__}",
                retry_safe=True,
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=cfg.timeout_seconds
            )
        except asyncio.CancelledError:
            await self._stop_welcome_process(proc)
            raise
        except TimeoutError as exc:
            await self._stop_welcome_process(proc)
            raise WelcomeDeliveryError(
                "OpenClaw delivery timed out; delivery result is uncertain",
                retry_safe=False,
            ) from exc

        delivery_status = self._openclaw_delivery_status(stdout)
        status = str((delivery_status or {}).get("status") or "")
        result_count = (delivery_status or {}).get("resultCount")
        if status == "sent" and isinstance(result_count, int) and result_count > 0:
            return

        detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        retry_safe = status in {"failed", "suppressed"}
        if not status and proc.returncode == 127 and "node" in detail.lower():
            retry_safe = True
        raise WelcomeDeliveryError(
            detail[:500]
            or f"OpenClaw delivery was not confirmed (exit={proc.returncode}, status={status or 'missing'})",
            retry_safe=retry_safe,
        )

    @staticmethod
    async def _stop_welcome_process(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.communicate()

    @staticmethod
    def _openclaw_delivery_status(stdout: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        status = payload.get("deliveryStatus")
        if not isinstance(status, dict):
            result = payload.get("result")
            status = result.get("deliveryStatus") if isinstance(result, dict) else None
        return status if isinstance(status, dict) else None

    async def _recover_pending_welcomes(self, guild: discord.Guild) -> None:
        await asyncio.sleep(1)
        if self.welcome_state is None:
            return
        pending = dict(self._read_welcome_state()["pending"])
        retryable_statuses = {
            "starter_created",
            "destination_created",
            "retryable_failure",
        }
        for raw_member_id, record in pending.items():
            if not isinstance(record, dict):
                continue
            status = str(record.get("status") or "")
            if status not in retryable_statuses:
                await self._log(
                    f"welcome recovery skipped member={raw_member_id} reason={status or 'invalid_status'}"
                )
                continue
            try:
                member_id = int(raw_member_id)
            except ValueError:
                continue
            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except Exception as exc:
                    await self._log(
                        f"welcome recovery member unavailable member={member_id} error={type(exc).__name__}"
                    )
                    continue
            await self._send_welcome_if_enabled(
                member=member,
                invite_type=str(record.get("inviteType") or "unknown"),
                role_names=str(record.get("roles") or "unknown"),
            )

    def _build_followup_agent_prompt(
        self, *, member: discord.Member, destination_channel_id: int
    ) -> str:
        cfg = self.config.welcome
        assert cfg is not None
        return (
            "あなたはAGIラボDiscordにいるラボちゃん本人です。\n"
            "参加から約1日たち、公開の自己紹介がまだ見つからない新メンバーへ、"
            "本人の歓迎スレッドでフォローを1通だけ送ります。\n\n"
            "必ず踏む手順:\n"
            "1. AGENTS.md/VISION.md/TOOLS.mdとcommunity-welcome Skillに従う。\n"
            "2. 公開範囲の直近の発言を軽く確認し、本人の発言があれば一つだけ自然に拾う。"
            "なければ無理に個人化しない。\n"
            "3. 最近のAGIラボで初参加者にも意味が通る話題があれば、一つだけ添えてよい。\n\n"
            "出力ルール:\n"
            "- 最終出力はそのままDiscordへ投稿される本文だけ。2〜4文、最大2段落。\n"
            "- 対象者を必ずDiscord mentionで呼ぶ。対象者以外、ロール、@everyone、@hereをmentionしない。\n"
            "- 全体案内と自己紹介チャンネルを実リンクで必ず案内する。\n"
            "- 『自己紹介の席、まだ空いてますよ？』くらいの、軽い生意気さは歓迎。"
            "罪悪感、急かし、監視されている感じは出さない。\n"
            "- 閲覧だけでも歓迎だと分かる余白を残し、自己紹介は1〜3文でよいと伝える。\n"
            "- 毎回同じ書き出し・締め・質問にしない。見つかった事実だけを使う。\n"
            "- 自動処理、内部状態、検索、ID一覧、配送事情には触れない。\n"
            "- 声はAGENTS.md/IDENTITY.md/SOUL.mdの現在のラボちゃんに従う。\n\n"
            f"対象者: <@{member.id}>\n"
            f"- 全体案内: <#{cfg.guide_channel_id}>\n"
            f"- 自己紹介: <#{cfg.intro_channel_id}>\n"
            f"- 投稿先: <#{destination_channel_id}>\n"
        )

    def _discrawl_member_has_intro(self, member_id: int) -> bool | None:
        cfg = self.config.welcome
        if cfg is None or cfg.followup is None or cfg.intro_channel_id is None:
            return None
        followup = cfg.followup
        try:
            age_seconds = datetime.now(timezone.utc).timestamp() - os.path.getmtime(
                followup.discrawl_db_path
            )
            if age_seconds > followup.archive_max_age_seconds:
                return None
            uri = f"file:{followup.discrawl_db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                found = connection.execute(
                    """
                    SELECT 1
                    FROM messages AS m
                    LEFT JOIN channels AS c ON c.id = m.channel_id
                    WHERE m.guild_id = ?
                      AND m.author_id = ?
                      AND m.deleted_at IS NULL
                      AND (
                        m.channel_id = ?
                        OR (
                          (c.parent_id = ? OR c.thread_parent_id = ?)
                          AND COALESCE(c.is_private_thread, 0) = 0
                        )
                      )
                    LIMIT 1
                    """,
                    (
                        str(self.config.guild_id),
                        str(member_id),
                        str(cfg.intro_channel_id),
                        str(cfg.intro_channel_id),
                        str(cfg.intro_channel_id),
                    ),
                ).fetchone()
            return found is not None
        except (OSError, sqlite3.Error):
            self.logger.exception(
                "followup intro lookup failed for member=%s", member_id
            )
            return None

    async def _welcome_followup_loop(self, guild: discord.Guild) -> None:
        cfg = self.config.welcome
        assert cfg is not None and cfg.followup is not None
        await asyncio.sleep(5)
        while True:
            try:
                await self._process_due_welcome_followups(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("welcome followup sweep failed")
            await asyncio.sleep(cfg.followup.check_interval_seconds)

    async def _process_due_welcome_followups(self, guild: discord.Guild) -> None:
        cfg = self.config.welcome
        assert cfg is not None and cfg.followup is not None
        if not cfg.followup.enabled or self.welcome_state is None:
            return

        now = datetime.now(timezone.utc)
        welcomed = dict(self._read_welcome_state()["welcomed"])
        for raw_member_id, welcome_record in welcomed.items():
            if not isinstance(welcome_record, dict):
                continue
            try:
                member_id = int(raw_member_id)
                welcomed_at = datetime.fromisoformat(
                    str(welcome_record["welcomedAt"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError):
                await self._log(
                    f"welcome followup skipped member={raw_member_id} reason=invalid_welcome_record"
                )
                continue
            if welcomed_at.tzinfo is None:
                welcomed_at = welcomed_at.replace(tzinfo=timezone.utc)
            if now < welcomed_at + timedelta(seconds=cfg.followup.delay_seconds):
                continue

            existing = self._followup_record_for(member_id)
            existing_status = str((existing or {}).get("status") or "")
            if existing_status and existing_status != "retryable_failure":
                continue
            attempts = int((existing or {}).get("attempts") or 0)
            if attempts >= cfg.max_attempts:
                continue

            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.NotFound:
                    self._mark_followup(member_id=member_id, status="skipped_left")
                    await self._log(
                        f"welcome followup skipped member={member_id} reason=left_guild"
                    )
                    continue
                except Exception as exc:
                    await self._log(
                        f"welcome followup deferred member={member_id} reason=member_lookup error={type(exc).__name__}"
                    )
                    continue
            if cfg.skip_bots and member.bot:
                self._mark_followup(member_id=member_id, status="skipped_bot")
                continue

            has_intro = self._discrawl_member_has_intro(member_id)
            if has_intro is None:
                await self._log(
                    f"welcome followup deferred member={member_id} reason=discrawl_unavailable_or_stale"
                )
                return
            if has_intro:
                self._mark_followup(member_id=member_id, status="skipped_intro_present")
                await self._log(
                    f"welcome followup skipped member={member_id} reason=intro_present"
                )
                continue

            thread_id = welcome_record.get("threadId")
            if not isinstance(thread_id, int) or thread_id <= 0:
                self._mark_followup(member_id=member_id, status="skipped_no_thread")
                continue

            attempts += 1
            attempt_started_at = datetime.now(timezone.utc)
            self._mark_followup(
                member_id=member_id,
                status="delivery_started",
                thread_id=thread_id,
                attempts=attempts,
            )
            prompt = self._build_followup_agent_prompt(
                member=member, destination_channel_id=thread_id
            )
            try:
                await self._run_openclaw_agent_welcome(
                    prompt=prompt,
                    member_id=member_id,
                    destination_channel_id=thread_id,
                    session_scope="welcome-followup",
                )
            except Exception as exc:
                if await self._welcome_message_exists_since(
                    destination_channel_id=thread_id,
                    member_id=member_id,
                    since=attempt_started_at,
                ):
                    self._mark_followup(
                        member_id=member_id,
                        status="sent",
                        thread_id=thread_id,
                        attempts=attempts,
                    )
                    continue
                retry_safe = isinstance(exc, WelcomeDeliveryError) and exc.retry_safe
                self._mark_followup(
                    member_id=member_id,
                    status=(
                        "retryable_failure" if retry_safe else "delivery_uncertain"
                    ),
                    thread_id=thread_id,
                    attempts=attempts,
                    error_type=type(exc).__name__,
                )
                await self._log(
                    f"welcome followup failed member={member_id} attempt={attempts} retry_safe={retry_safe} error={type(exc).__name__}"
                )
                continue

            self._mark_followup(
                member_id=member_id,
                status="sent",
                thread_id=thread_id,
                attempts=attempts,
            )
            await self._log(
                f"welcome followup sent member={member_id} thread={thread_id}"
            )

    async def _validate_welcome_setup(self, guild: discord.Guild) -> None:
        cfg = self.config.welcome
        assert cfg is not None
        bot_member = await self._resolve_bot_member(guild)
        channel_ok = False
        permissions_ok = False
        try:
            channel = await self._welcome_parent_channel()
            channel_ok = True
            if bot_member is not None:
                permissions = channel.permissions_for(bot_member)
                permissions_ok = bool(
                    permissions.view_channel
                    and permissions.read_message_history
                    and permissions.send_messages
                    and (
                        not cfg.threaded
                        or (
                            permissions.create_public_threads
                            and permissions.send_messages_in_threads
                        )
                    )
                )
        except Exception as exc:
            self.logger.warning("welcome channel validation failed: %s", exc)

        path = os.environ.get("PATH", "")
        if cfg.node_bin_dir is not None:
            path = f"{cfg.node_bin_dir}:{path}"
        executable_ok = (
            os.access(cfg.openclaw_bin, os.X_OK)
            if os.path.sep in cfg.openclaw_bin
            else shutil.which(cfg.openclaw_bin, path=path) is not None
        )
        node_ok = shutil.which("node", path=path) is not None
        cwd_ok = cfg.openclaw_cwd.is_dir()
        self.logger.info(
            "welcome ready channel=%s channel_ok=%s permissions_ok=%s openclaw_ok=%s node_ok=%s cwd_ok=%s threaded=%s",
            self._welcome_channel_id(),
            channel_ok,
            permissions_ok,
            executable_ok,
            node_ok,
            cwd_ok,
            cfg.threaded,
        )

    async def _respond_ephemeral(
        self, interaction: discord.Interaction, content: str
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    async def _write_heartbeat(self, *, status: str) -> None:
        payload: dict[str, Any] = {
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "guildId": self.config.guild_id,
            "connected": not self.is_closed(),
            "latency": self.latency,
            "inviteCount": len(self._invite_uses),
        }
        if self.user is not None:
            payload["botUserId"] = self.user.id
        self.heartbeat_state.write(payload)

    async def _log(self, content: str) -> None:
        self.logger.info(content)
        if self.config.log_channel_id is None:
            return
        channel = self.get_channel(self.config.log_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.config.log_channel_id)
            except Exception:
                return
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                await channel.send(content[:1900])
            except Exception:
                self.logger.warning("failed to send Discord log: %s", content)

    def run_forever(self) -> None:
        self.run(self.config.discord_token, log_handler=None)
