from __future__ import annotations

import asyncio
import time
from typing import Iterable, List

import discord

from src.core.agent_base import BaseAgent
from src.core.discord_access import DiscordAccessPolicy, is_message_allowed, load_discord_access_policy
from src.core.discord_search import DiscordSearchResult, search_messages_discord
from src.core.llm import get_llm_client
from src.core import config as core_config

from .config import ClaudeSearchConfig, load_config
from . import prompts


ERR_RATE_LIMIT = "連続リクエストが多すぎます。少し待ってから再度お試しください。"
ERR_NO_QUERY = "検索キーワードを入力してください。例: `!csearch キーワード`"
ERR_NO_RESULTS = "該当するメッセージが見つかりませんでした。"
ERR_LLM_KEY_MISSING = "OpenRouter API キーが未設定です。"
ERR_DISCORD_SEARCH = "Discord 検索に失敗しました。少し時間をおいてください。"
ERR_LLM = "すみません、回答の生成に失敗しました。少し時間をおいてください。"


class ClaudeSearchAgent(BaseAgent):
    def __init__(self) -> None:
        self.config: ClaudeSearchConfig = load_config()
        self._access_policy: DiscordAccessPolicy = load_discord_access_policy()
        self._client: discord.Client | None = None
        self._last_request_by_user: dict[int, float] = {}
        self._log_channel: discord.TextChannel | None = None
        self._llm = get_llm_client(self.config.llm_provider, self.config.llm_model)
        self._discord_token = core_config.DISCORD_TOKEN or ""

    @property
    def name(self) -> str:
        return "claude_search"

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        if self.config.log_channel_id:
            channel = client.get_channel(self.config.log_channel_id)
            if isinstance(channel, discord.TextChannel):
                self._log_channel = channel

        if self.config.enabled:
            print("ClaudeSearchAgent is ready.")
        if self.config.debug:
            self._debug(
                f"allowed_channels={self._access_policy.allowed_channel_ids or 'ALL'} "
                f"require_mention={self._access_policy.require_mention} "
                f"search_limit={self.config.discord_search_limit} max_results={self.config.max_results}"
            )

    async def on_message(self, message: discord.Message) -> None:
        if not self.config.enabled:
            return
        if message.author.bot:
            return
        if not message.guild:
            return
        if not is_message_allowed(message, self._access_policy, self._client.user if self._client else None):
            return

        content = (message.content or "").strip()
        if not content:
            return

        if content.startswith("!csearch"):
            query = content[len("!csearch") :].strip()
            await self._handle_search(message, query)
            return

        if content.startswith("!cask"):
            question = content[len("!cask") :].strip()
            await self._handle_ask(message, question)
            return

    def _is_rate_limited(self, user_id: int) -> bool:
        now = time.time()
        last = self._last_request_by_user.get(user_id)
        if last and (now - last) < self.config.rate_limit_seconds:
            return True
        self._last_request_by_user[user_id] = now
        return False

    async def _handle_search(self, message: discord.Message, query: str) -> None:
        if self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return
        if not query:
            await message.channel.send(ERR_NO_QUERY)
            return

        results = await self._search(message, query)
        if results is None:
            await message.channel.send(ERR_DISCORD_SEARCH)
            return
        if not results:
            await message.channel.send(ERR_NO_RESULTS)
            return

        lines = ["検索結果:"]
        for idx, result in enumerate(results, start=1):
            snippet = self._truncate(result.content, 120)
            lines.append(f"{idx}. #{result.channel_name} {result.author_name}: {snippet}")
            lines.append(result.jump_url)

        await self._send_lines(message.channel, lines)

    async def _handle_ask(self, message: discord.Message, question: str) -> None:
        if self._is_rate_limited(message.author.id):
            await message.channel.send(ERR_RATE_LIMIT)
            return
        if not question:
            await message.channel.send("質問内容を入力してください。例: `!cask この議題の結論は？`")
            return

        await message.channel.send("🕵️‍♀️ 検索中です...")
        results = await self._search(message, question)
        if results is None:
            await message.channel.send(ERR_DISCORD_SEARCH)
            return
        if not results:
            await message.channel.send(ERR_NO_RESULTS)
            return

        answer = await self._ask_llm(question, results)
        if not answer:
            await message.channel.send(ERR_LLM)
            return

        sources = self._format_sources(results[:3])
        await self._send_lines(message.channel, [answer, "", "Sources:", sources])

    async def _search(self, message: discord.Message, query: str) -> List[DiscordSearchResult] | None:
        if not self._discord_token:
            self._debug("DISCORD_TOKEN not set; cannot call search API")
            return None
        if not message.guild:
            return None
        guild = message.guild
        raw_results = await search_messages_discord(
            guild_id=guild.id,
            query=query,
            bot_token=self._discord_token,
            channel_ids=self._resolve_allowed_channel_ids_for_guild(guild),
            author_ids=None,
            limit=self.config.discord_search_limit,
            resolve_channel_name=self._resolve_channel_name,
        )
        if raw_results is None:
            return None
        if not raw_results:
            return []
        filtered = self._filter_results_for_member(message, raw_results)
        return filtered[: self.config.max_results]

    def _filter_results_for_member(
        self, message: discord.Message, results: List[DiscordSearchResult]
    ) -> List[DiscordSearchResult]:
        if not self._client or not message.guild:
            return results
        if not isinstance(message.author, discord.Member):
            return results
        member: discord.Member = message.author
        filtered: List[DiscordSearchResult] = []
        for result in results:
            channel = self._client.get_channel(result.channel_id)
            if not channel:
                continue
            try:
                if not isinstance(channel, (discord.abc.GuildChannel, discord.Thread)):
                    continue
                perms = channel.permissions_for(member)
            except Exception:
                continue
            if hasattr(perms, "view_channel") and not perms.view_channel:
                continue
            filtered.append(result)
        return filtered

    def _resolve_allowed_channel_ids_for_guild(self, guild: discord.Guild) -> List[int]:
        if not self._access_policy.allowed_channel_ids or not self._client:
            return []
        filtered: List[int] = []
        for channel_id in self._access_policy.allowed_channel_ids:
            channel = self._client.get_channel(channel_id)
            if isinstance(channel, discord.abc.GuildChannel) and channel.guild.id == guild.id:
                filtered.append(channel_id)
        return filtered

    def _resolve_channel_name(self, channel_id: int) -> str:
        if not self._client:
            return "unknown"
        channel = self._client.get_channel(channel_id)
        if channel and hasattr(channel, "name"):
            return getattr(channel, "name", "unknown")
        return "unknown"

    async def _ask_llm(self, question: str, results: List[DiscordSearchResult]) -> str:
        context = self._format_context(results)
        prompt = prompts.ANSWER_PROMPT_TEMPLATE.format(question=question, context=context)
        try:
            return await asyncio.wait_for(self._run_query(prompt), timeout=30)
        except asyncio.TimeoutError:
            self._debug("LLM query timeout")
            return ERR_LLM
        except Exception as exc:
            self._debug(f"LLM query error: {exc}")
            if isinstance(exc, RuntimeError) and "OPENROUTER_API_KEY" in str(exc):
                return ERR_LLM_KEY_MISSING
            return ERR_LLM

    async def _run_query(self, prompt: str) -> str:
        result = await self._llm.generate(
            prompt,
            temperature=self.config.llm_temperature,
            max_output_tokens=self.config.llm_max_output_tokens,
        )
        return result.text.strip() if result.text else ""

    def _format_context(self, results: List[DiscordSearchResult]) -> str:
        lines = []
        for idx, result in enumerate(results, start=1):
            snippet = self._truncate(result.content, 400)
            lines.append(
                f"[{idx}] #{result.channel_name} {result.author_name} ({result.created_at})\n"
                f"{snippet}\n"
                f"{result.jump_url}"
            )
        return "\n\n".join(lines)

    def _format_sources(self, results: List[DiscordSearchResult]) -> str:
        lines = []
        for idx, result in enumerate(results, start=1):
            lines.append(f"{idx}. {result.jump_url}")
        return "\n".join(lines)

    async def _send_lines(self, channel: discord.abc.Messageable, lines: Iterable[str]) -> None:
        def split_line(text: str, limit: int = 1900) -> List[str]:
            if len(text) <= limit:
                return [text]
            return [text[i : i + limit] for i in range(0, len(text), limit)]

        buffer = ""
        for line in lines:
            for chunk in split_line(line):
                candidate = f"{buffer}\n{chunk}" if buffer else chunk
                if len(candidate) > 1900:
                    if buffer:
                        await channel.send(buffer)
                    buffer = chunk
                else:
                    buffer = candidate
        if buffer:
            await channel.send(buffer)

    def _truncate(self, text: str, limit: int) -> str:
        clean = " ".join(text.split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 1] + "…"

    def _debug(self, message: str) -> None:
        if self.config.debug:
            print(f"[claude_search] {message}")
        if self._log_channel:
            try:
                asyncio.create_task(self._log_channel.send(f"[claude_search] {message}"))
            except Exception:
                pass
