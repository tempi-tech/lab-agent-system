from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Iterable, List, Optional

import discord

from src.core.agent_base import BaseAgent
from src.core.llm import get_llm_client
from src.core.llm import LLMClient

from .config import UpdatesAssistantConfig, load_config
from . import prompts
from .router import RouterDecision, parse_router_decision


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
        self._recent_message_ids: dict[int, float] = {}
        self._recent_messages_by_channel: Dict[int, Deque[dict]] = {}
        self._llm_client: LLMClient | None = None
        self._router_llm: LLMClient | None = None
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
        if not message.guild:
            return
        if message.author.bot:
            if self.config.context_include_bots:
                if not self._is_duplicate_message(message.id) and self._is_allowed_channel(message.channel):
                    self._record_message(message)
            return
        if self._is_duplicate_message(message.id):
            self._debug(f"ignored: duplicate message_id={message.id}")
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
            if self.config.chat_only:
                await message.channel.send("今は雑談専用モードですッス！メンションで話しかけてくださいッス！")
                return
            await self._debug_send(f"!updates from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(f"command !updates by {message.author} content={content!r}")
            period = self._parse_period(content)
            await self._handle_updates(message, period)
            return

        if content.startswith("!ask"):
            if self.config.chat_only:
                await message.channel.send("今は雑談専用モードですッス！メンションで話しかけてくださいッス！")
                return
            await self._debug_send(f"!ask from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(f"command !ask by {message.author} content={content!r}")
            question = content[len("!ask"):].strip()
            if not question:
                await message.channel.send("質問内容を入れてくださいッス！")
                return
            period = self._parse_period(self.config.default_period)
            await self._handle_ask(message, question, period)
            return

        mentioned = self._is_mentioned(message)
        if not mentioned and self.config.reply_as_mention:
            mentioned = await self._is_reply_to_bot(message)

        if mentioned:
            await self._debug_send(f"mention from {message.author} in #{getattr(message.channel, 'name', 'unknown')}")
            self._debug(f"mentioned by {message.author} content={content!r}")
            await self._handle_mention(message)
            self._record_message(message)
            return

        # Store non-mention messages for short-term context
        self._record_message(message)

    def _is_allowed_channel(self, channel: discord.abc.GuildChannel) -> bool:
        allowed = self.config.allowed_channel_ids
        if not allowed:
            return True
        return channel.id in allowed

    def _is_mentioned(self, message: discord.Message) -> bool:
        if not self._client or not self._client.user:
            return False
        return self._client.user in message.mentions

    async def _is_reply_to_bot(self, message: discord.Message) -> bool:
        if not self._client or not self._client.user:
            return False
        ref = getattr(message, "reference", None)
        if not ref or not getattr(ref, "message_id", None):
            return False
        if getattr(ref, "resolved", None) and isinstance(ref.resolved, discord.Message):
            return ref.resolved.author.id == self._client.user.id
        try:
            if isinstance(message.channel, discord.TextChannel):
                replied = await message.channel.fetch_message(ref.message_id)
                return replied.author.id == self._client.user.id
        except Exception:
            return False
        return False

    def _is_rate_limited(self, user_id: int) -> bool:
        if self.config.rate_limit_seconds <= 0:
            return False
        now = time.time()
        last = self._last_request_by_user.get(user_id)
        if last and (now - last) < self.config.rate_limit_seconds:
            self._debug(f"rate limited user_id={user_id}")
            return True
        self._last_request_by_user[user_id] = now
        return False

    def _is_duplicate_message(self, message_id: int) -> bool:
        now = time.time()
        self._recent_message_ids = {
            mid: ts for mid, ts in self._recent_message_ids.items() if (now - ts) < 60
        }
        if message_id in self._recent_message_ids:
            return True
        self._recent_message_ids[message_id] = now
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

    async def _handle_updates(
        self,
        message: discord.Message,
        period: str,
        channels: Iterable[discord.abc.GuildChannel] | None = None,
        rate_limit_checked: bool = False,
    ) -> None:
        if not rate_limit_checked and self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return

        self._debug(f"updates: fetching logs period={period}")
        await self._react(message)
        try:
            logs = await asyncio.wait_for(
                self._fetch_logs(message.guild, period, requester=message.author, channels=channels),
                timeout=20,
            )
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
        channels: Iterable[discord.abc.GuildChannel] | None = None,
    ) -> None:
        if not rate_limit_checked and self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return

        self._debug(f"ask: fetching logs period={period} question={question!r}")
        await self._react(message)
        try:
            logs = await asyncio.wait_for(
                self._fetch_logs(message.guild, period, requester=message.author, channels=channels),
                timeout=20,
            )
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

        await self._react(message)
        content = message.content.replace(self._client.user.mention, "").strip() if self._client else message.content
        context = await self._build_context(message)
        if self.config.chat_only:
            self._debug("mention: chat-only mode")
            prompt = prompts.CHAT_PROMPT_TEMPLATE.format(
                persona=prompts.BASE_PERSONA,
                context=context,
                message=content,
            )
            text = await self._generate_text(prompt)
            await message.channel.send(text or ERR_LLM)
            return
        decision = await self._route_mention(content)
        self._debug(f"mention: routed action={decision.action} period={decision.period} scope={decision.scope}")
        channels = [message.channel] if decision.scope == "channel" else None
        if decision.action == "log_summary":
            await self._handle_updates(
                message,
                decision.period,
                channels=channels,
                rate_limit_checked=True,
            )
            return
        if decision.action == "log_qa":
            await self._handle_ask(
                message,
                content,
                decision.period,
                rate_limit_checked=True,
                channels=channels,
            )
            return

        self._debug("mention: chat mode")
        prompt = prompts.CHAT_PROMPT_TEMPLATE.format(
            persona=prompts.BASE_PERSONA,
            context=context,
            message=content,
        )
        text = await self._generate_text(prompt)
        await message.channel.send(text or ERR_LLM)

    async def _route_mention(self, content: str) -> RouterDecision:
        if not self.config.router_enabled:
            return RouterDecision(action="chat", period=self._parse_period(self.config.default_period), scope=self.config.router_default_scope)
        if not self._router_llm:
            self._router_llm = get_llm_client(self.config.router_llm_provider, self.config.router_llm_model)

        default_period = self._parse_period(self.config.default_period)
        default_scope = self.config.router_default_scope if self.config.router_default_scope in {"channel", "guild"} else "channel"
        prompt = prompts.ROUTER_PROMPT_TEMPLATE.format(
            message=content,
            period_options=sorted(PERIOD_OPTIONS),
            default_period=default_period,
            default_scope=default_scope,
        )
        try:
            result = await asyncio.wait_for(
                self._router_llm.generate(
                    prompt,
                    temperature=self.config.router_llm_temperature,
                    max_output_tokens=self.config.router_llm_max_output_tokens,
                ),
                timeout=10,
            )
            return parse_router_decision(
                result.text,
                default_period=default_period,
                default_scope=default_scope,
            )
        except asyncio.TimeoutError:
            self._debug("router: timeout")
        except Exception as exc:
            self._debug(f"router: error {type(exc).__name__}: {exc}")
        return RouterDecision(action="chat", period=default_period, scope=default_scope)

    async def _fetch_logs(
        self,
        guild: discord.Guild,
        period: str,
        *,
        requester: discord.abc.User | discord.Member,
        channels: Iterable[discord.abc.GuildChannel] | None = None,
    ) -> List[str] | None:
        since = datetime.now(timezone.utc) - self._period_to_timedelta(period)
        target_channels = list(channels) if channels is not None else self._get_target_channels(guild)
        lines: List[str] = []
        total_count = 0

        self._debug(f"fetch_logs: channels={len(target_channels)} since={since}")
        try:
            for channel in target_channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                perms = channel.permissions_for(guild.me) if guild.me else None
                if perms and (not perms.view_channel or not perms.read_message_history):
                    self._debug(f"fetch_logs: skip no-perms #{channel.name}")
                    continue
                if not self._can_read_channel(channel, requester):
                    self._debug(f"fetch_logs: skip requester-no-perms #{channel.name}")
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

    def _can_read_channel(self, channel: discord.TextChannel, user: discord.abc.User | discord.Member) -> bool:
        if not isinstance(user, discord.Member):
            return False
        perms = channel.permissions_for(user)
        return perms.view_channel and perms.read_message_history

    async def _generate_text(self, prompt: str) -> str:
        if self._llm_client is None:
            self._llm_client = get_llm_client(self.config.llm_provider, self.config.llm_model)

        try:
            result = await asyncio.wait_for(
                self._llm_client.generate(prompt, temperature=0.2, max_output_tokens=800),
                timeout=30,
            )
            self._debug("LLM response received")
            return (result.text or "").strip()
        except Exception as exc:
            self._debug(f"LLM error: {type(exc).__name__}: {exc}")
            return ERR_LLM

    def _record_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if message.author.bot and not self.config.context_include_bots:
            return
        if self.config.context_max_messages <= 0:
            return
        content = (message.content or "").strip()
        if not content and message.attachments:
            content = f"[添付ファイル: {message.attachments[0].filename}]"
        if not content:
            return
        channel_id = message.channel.id
        bucket = self._recent_messages_by_channel.get(channel_id)
        if bucket is None:
            bucket = deque(maxlen=self.config.context_max_messages)
            self._recent_messages_by_channel[channel_id] = bucket
        bucket.append(
            {
                "id": message.id,
                "author": getattr(message.author, "display_name", None) or getattr(message.author, "name", "unknown"),
                "content": content,
            }
        )

    async def _build_context(self, message: discord.Message) -> str:
        lines: List[str] = []
        reply_context = await self._get_reply_context(message)
        if reply_context:
            lines.append(f"[返信先] {reply_context}")

        channel_id = message.channel.id
        bucket = self._recent_messages_by_channel.get(channel_id)
        if bucket:
            for item in list(bucket):
                if item.get("id") == message.id:
                    continue
                lines.append(f"{item.get('author')}: {item.get('content')}")
        if not lines:
            return "（なし）"
        return "\n".join(lines[-self.config.context_max_messages :])

    async def _get_reply_context(self, message: discord.Message) -> Optional[str]:
        ref = getattr(message, "reference", None)
        if not ref or not ref.message_id:
            return None
        target: Optional[discord.Message] = None
        if getattr(ref, "resolved", None) and isinstance(ref.resolved, discord.Message):
            target = ref.resolved
        else:
            try:
                if isinstance(message.channel, discord.TextChannel):
                    target = await message.channel.fetch_message(ref.message_id)
            except Exception:
                target = None
        if not target:
            return None
        content = (target.content or "").strip()
        if not content and target.attachments:
            content = f"[添付ファイル: {target.attachments[0].filename}]"
        if not content:
            return None
        author = getattr(target.author, "display_name", None) or getattr(target.author, "name", "unknown")
        return f"{author}: {content}"

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

    def _resolve_reaction_emoji(self, message: discord.Message, raw: str) -> str | discord.Emoji:
        if raw.startswith("<:") or raw.startswith("<a:"):
            return raw
        name = raw
        if raw.startswith(":") and raw.endswith(":") and len(raw) > 2:
            name = raw[1:-1].strip()
        if name and message.guild:
            for emoji in message.guild.emojis:
                if emoji.name == name:
                    return emoji
        return raw

    async def _react(self, message: discord.Message) -> None:
        raw = (self.config.reaction_emoji or "").strip()
        if not raw:
            return
        try:
            emoji = self._resolve_reaction_emoji(message, raw)
            await message.add_reaction(emoji)
        except Exception:
            return
