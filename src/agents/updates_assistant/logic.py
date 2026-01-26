from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

import discord
from google import genai

from src.core.agent_base import BaseAgent
from src.core import config as core_config

from .config import UpdatesAssistantConfig, load_config
from . import prompts


PERIOD_OPTIONS = {"1h", "6h", "24h", "7d"}
MAX_MESSAGES_PER_CHANNEL = 200
MAX_MESSAGES_TOTAL = 600

ERR_LLM = "すみません、ちょっと調子が悪いッス...少し待ってから試してくださいッス！"
ERR_RATE_LIMIT = "センパイ、少し落ち着いてくださいッス！30秒後にまた聞いてくださいッス！"
ERR_LOG_FETCH = "ログの取得に失敗しましたッス..."


class UpdatesAssistantAgent(BaseAgent):
    def __init__(self) -> None:
        self.config: UpdatesAssistantConfig = load_config()
        self._client: discord.Client | None = None
        self._last_request_by_user: dict[int, float] = {}
        self._llm_client: genai.Client | None = None
        self._log_channel: discord.TextChannel | None = None

    @property
    def name(self) -> str:
        return "updates_assistant"

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        # ログチャンネルを取得
        if self.config.log_channel_id:
            ch = client.get_channel(self.config.log_channel_id)
            if isinstance(ch, discord.TextChannel):
                self._log_channel = ch
        if self.config.enabled:
            print("UpdatesAssistantAgent is ready.")
        if self.config.debug:
            self._debug(
                f"config enabled={self.config.enabled} allowed_channels={self.config.allowed_channel_ids or 'ALL'} "
                f"rate_limit={self.config.rate_limit_seconds}s default_period={self.config.default_period} "
                f"model={self.config.llm_model} log_channel={self.config.log_channel_id}"
            )
            for guild in client.guilds:
                accessible = 0
                for channel in guild.text_channels:
                    perms = channel.permissions_for(guild.me) if guild.me else None
                    if perms and perms.view_channel and perms.read_message_history:
                        accessible += 1
                self._debug(f"guild={guild.name} text_channels={len(guild.text_channels)} accessible={accessible}")

    async def on_message(self, message: discord.Message) -> None:
        if not self.config.enabled:
            return
        self._debug(
            f"on_message guild={getattr(message.guild, 'name', None)} "
            f"channel=#{getattr(message.channel, 'name', 'unknown')} "
            f"author={message.author} content_len={len(message.content or '')}"
        )
        if message.author.bot:
            return
        if not message.guild:
            return
        if not self._is_allowed_channel(message.channel):
            self._debug(f"ignored: channel not allowed #{getattr(message.channel, 'name', 'unknown')}")
            return

        content = message.content.strip()
        if not content:
            await self._debug_send(f"empty content from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(
                f"ignored: empty content from {message.author} in "
                f"#{getattr(message.channel, 'name', 'unknown')} (check message_content intent)"
            )
            return

        if content.startswith("!updates"):
            await self._debug_send(f"!updates from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(f"command !updates by {message.author} content={content!r}")
            period = self._parse_period(content)
            await self._handle_updates(message, period)
            return

        if content.startswith("!ask"):
            await self._debug_send(f"!ask from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(f"command !ask by {message.author} content={content!r}")
            question = content[len("!ask"):].strip()
            if not question:
                await message.channel.send("質問内容を入れてくださいッス！")
                return
            period = self._parse_period(self.config.default_period)
            await self._handle_ask(message, question, period)
            return

        if self._is_mentioned(message):
            await self._debug_send(f"mention from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(f"mentioned by {message.author} content={content!r}")
            await self._handle_mention(message)

    def _is_allowed_channel(self, channel: discord.abc.GuildChannel) -> bool:
        allowed = self.config.allowed_channel_ids
        if not allowed:
            return True
        return channel.id in allowed

    def _is_mentioned(self, message: discord.Message) -> bool:
        if not self._client or not self._client.user:
            return False
        return self._client.user in message.mentions

    def _is_rate_limited(self, user_id: int) -> bool:
        now = time.time()
        last = self._last_request_by_user.get(user_id)
        if last and (now - last) < self.config.rate_limit_seconds:
            self._debug(f"rate limited user_id={user_id}")
            return True
        self._last_request_by_user[user_id] = now
        return False

    def _parse_period(self, content: str) -> str:
        token = content.strip().split()
        if len(token) >= 2:
            candidate = token[1].lower()
            if candidate in PERIOD_OPTIONS:
                return candidate
        default = self.config.default_period.lower()
        return default if default in PERIOD_OPTIONS else "24h"

    def _period_to_timedelta(self, period: str) -> timedelta:
        if period.endswith("h"):
            return timedelta(hours=int(period[:-1]))
        if period.endswith("d"):
            return timedelta(days=int(period[:-1]))
        return timedelta(hours=24)

    async def _handle_updates(self, message: discord.Message, period: str) -> None:
        if self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return

        self._debug(f"updates: fetching logs period={period}")
        await message.channel.send("🕵️‍♀️ 直近ログを確認中ッス！")
        try:
            logs = await asyncio.wait_for(self._fetch_logs(message.guild, period), timeout=20)
        except asyncio.TimeoutError:
            self._debug("updates: fetch_logs timeout")
            await message.channel.send(ERR_LOG_FETCH)
            return
        if logs is None:
            await message.channel.send(ERR_LOG_FETCH)
            return
        if not logs:
            await message.channel.send(f"直近{period}では大きな更新は見当たらなかったッス！")
            return

        self._debug(f"updates: logs lines={len(logs)}")
        prompt = prompts.SUMMARY_PROMPT_TEMPLATE.format(
            persona=prompts.BASE_PERSONA,
            period=period,
            logs="\n".join(logs),
        )
        text = await self._generate_text(prompt)
        await message.channel.send(text or ERR_LLM)

    async def _handle_ask(
        self,
        message: discord.Message,
        question: str,
        period: str,
        rate_limit_checked: bool = False,
    ) -> None:
        if not rate_limit_checked and self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return

        self._debug(f"ask: fetching logs period={period} question={question!r}")
        await message.channel.send("🕵️‍♀️ ログを確認中ッス！")
        try:
            logs = await asyncio.wait_for(self._fetch_logs(message.guild, period), timeout=20)
        except asyncio.TimeoutError:
            self._debug("ask: fetch_logs timeout")
            await message.channel.send(ERR_LOG_FETCH)
            return
        if logs is None:
            await message.channel.send(ERR_LOG_FETCH)
            return
        if not logs:
            await message.channel.send("該当するログが見つかりませんでしたッス...")
            return

        self._debug(f"ask: logs lines={len(logs)}")
        prompt = prompts.QA_PROMPT_TEMPLATE.format(
            persona=prompts.BASE_PERSONA,
            period=period,
            question=question,
            logs="\n".join(logs),
        )
        text = await self._generate_text(prompt)
        await message.channel.send(text or ERR_LLM)

    async def _handle_mention(self, message: discord.Message) -> None:
        if self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return

        content = message.content.replace(self._client.user.mention, "").strip() if self._client else message.content
        if self._looks_like_question(content):
            period = self._parse_period(self.config.default_period)
            await self._handle_ask(message, content, period, rate_limit_checked=True)
            return

        self._debug("mention: chat mode")
        prompt = prompts.CHAT_PROMPT_TEMPLATE.format(
            persona=prompts.BASE_PERSONA,
            message=content,
        )
        text = await self._generate_text(prompt)
        await message.channel.send(text or ERR_LLM)

    def _looks_like_question(self, content: str) -> bool:
        lowered = content.lower()
        return any(
            key in lowered
            for key in ["?", "？", "教えて", "どうや", "どうすれば", "何", "なに", "どこ", "いつ", "誰", "方法"]
        )

    async def _fetch_logs(self, guild: discord.Guild, period: str) -> List[str] | None:
        since = datetime.now(timezone.utc) - self._period_to_timedelta(period)
        channels = self._get_target_channels(guild)
        lines: List[str] = []
        total_count = 0

        self._debug(f"fetch_logs: channels={len(channels)} since={since}")
        try:
            for channel in channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                perms = channel.permissions_for(guild.me) if guild.me else None
                if perms and (not perms.view_channel or not perms.read_message_history):
                    self._debug(f"fetch_logs: skip no-perms #{channel.name}")
                    continue
                count = 0
                async for msg in channel.history(after=since, limit=MAX_MESSAGES_PER_CHANNEL):
                    if msg.author.bot:
                        continue
                    content = msg.content.strip()
                    if not content and msg.attachments:
                        content = f"[添付ファイル: {msg.attachments[0].filename}]"
                    if not content:
                        continue
                    ts = msg.created_at.strftime("%Y-%m-%d %H:%M")
                    lines.append(f"[{ts}] #{channel.name} {msg.author.display_name}: {content} ({msg.jump_url})")
                    count += 1
                    total_count += 1
                    if count >= MAX_MESSAGES_PER_CHANNEL:
                        break
                    if total_count >= MAX_MESSAGES_TOTAL:
                        break
                if total_count >= MAX_MESSAGES_TOTAL:
                    break
            if len(lines) > MAX_MESSAGES_TOTAL:
                lines = lines[-MAX_MESSAGES_TOTAL:]
            self._debug(f"fetch_logs: collected={len(lines)}")
            return lines
        except Exception as exc:
            self._debug(f"fetch_logs error: {type(exc).__name__}: {exc}")
            return None

    def _get_target_channels(self, guild: discord.Guild) -> Iterable[discord.abc.GuildChannel]:
        if self.config.allowed_channel_ids:
            return [guild.get_channel(cid) for cid in self.config.allowed_channel_ids if guild.get_channel(cid)]
        return list(guild.text_channels)

    async def _generate_text(self, prompt: str) -> str:
        if not core_config.GOOGLE_API_KEY:
            self._debug("LLM error: GOOGLE_API_KEY is not set")
            return ERR_LLM
        if self._llm_client is None:
            self._llm_client = genai.Client(api_key=core_config.GOOGLE_API_KEY)

        def _generate_sync() -> str:
            resp = self._llm_client.models.generate_content(
                model=self.config.llm_model,
                contents=prompt,
            )
            return getattr(resp, "text", "") or ""

        try:
            text = await asyncio.to_thread(_generate_sync)
            self._debug("LLM response received")
            return text.strip()
        except Exception as exc:
            self._debug(f"LLM error: {type(exc).__name__}: {exc}")
            return ERR_LLM

    def _debug(self, message: str) -> None:
        if self.config.debug:
            print(f"[UpdatesAssistant] {message}")

    async def _debug_send(self, text: str) -> None:
        """デバッグメッセージをログチャンネルに送信（設定されている場合のみ）"""
        if not self.config.debug:
            return
        if not self._log_channel:
            return
        try:
            await self._log_channel.send(f"[UpdatesAssistant] {text}")
        except Exception:
            return
