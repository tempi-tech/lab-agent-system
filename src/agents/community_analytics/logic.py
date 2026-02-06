from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

import discord

from src.core.agent_base import BaseAgent
from src.core.discord_history import (
    iter_forum_messages,
    iter_text_channel_messages,
    iter_text_channel_thread_messages,
)
from src.core.discord_text import send_chunked

from .config import CommunityAnalyticsConfig, load_config
from .integrations import load_membership_summary, load_question_sla_summary
from .metrics import AnalyticsEvent, JST, compute_metrics
from .reporting import (
    build_weekly_payload,
    find_latest_weekly_report_path,
    format_weekly_report,
    load_weekly_payload,
)


class CommunityAnalyticsAgent(BaseAgent):
    def __init__(self) -> None:
        self.config: CommunityAnalyticsConfig = load_config()
        self._client: discord.Client | None = None
        self.action_namespace = "community_analytics"

    @property
    def name(self) -> str:
        return "community_analytics"

    def get_actions(self):
        return {
            "report": self._action_report,
        }

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        if self.config.debug:
            print("[community_analytics] ready")

    async def run_scheduled_report(self, *, target_channel_id: int | None = None) -> None:
        """
        Run a single analytics report (intended for `python main.py --once analytics`).

        If `target_channel_id` is provided, it overrides config.channel_id.
        """

        if not self._client:
            print("[community_analytics] not initialized")
            return

        channel_id = int(target_channel_id or 0) or int(self.config.channel_id or 0)
        if not channel_id:
            print("[community_analytics] COMMUNITY_ANALYTICS_CHANNEL_ID not set")
            return

        ch = self._client.get_channel(channel_id)
        if not isinstance(ch, discord.abc.Messageable):
            print(f"[community_analytics] channel not found/messageable: {channel_id}")
            return

        await self.generate_and_send_report(ch)

    async def _action_report(self, message: discord.Message, args: List[str]) -> None:
        if not self._client:
            return

        # `!agent community_analytics report here` posts to the current channel.
        target: discord.abc.Messageable | None = None
        if args and args[0].lower() == "here":
            target = message.channel
        else:
            channel_id = int(self.config.channel_id or 0)
            ch = self._client.get_channel(channel_id) if channel_id else None
            target = ch if isinstance(ch, discord.abc.Messageable) else None

        if not target:
            await message.channel.send("❌ COMMUNITY_ANALYTICS_CHANNEL_ID が未設定、またはチャンネルが見つかりません。")
            return

        await self.generate_and_send_report(target)

    def _resolve_source_channels(self) -> list[discord.abc.GuildChannel]:
        if not self._client:
            return []

        source_ids = set(self.config.source_channel_ids)
        for category_id in self.config.source_category_ids:
            category = self._client.get_channel(category_id)
            if isinstance(category, discord.CategoryChannel):
                for cat_ch in category.channels:
                    if isinstance(cat_ch, (discord.TextChannel, discord.ForumChannel)):
                        source_ids.add(int(cat_ch.id))
            else:
                if self.config.debug:
                    print(f"[community_analytics] category not found: {category_id}")

        source_ids -= self.config.source_channel_exclude_ids

        channels: list[discord.abc.GuildChannel] = []
        for cid in sorted(source_ids):
            ch = self._client.get_channel(cid)
            if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                channels.append(ch)
            else:
                if self.config.debug:
                    print(f"[community_analytics] channel not found/unsupported: {cid}")
        return channels

    async def _collect_events(
        self,
        *,
        after: datetime,
        channels: Sequence[discord.abc.GuildChannel],
    ) -> list[AnalyticsEvent]:
        events: list[AnalyticsEvent] = []

        for ch in channels:
            if isinstance(ch, discord.TextChannel):
                # Parent channel messages.
                async for msg in iter_text_channel_messages(ch, after=after):
                    e = _event_from_message(msg, parent_channel_id=ch.id, thread_id=None)
                    if e:
                        events.append(e)

                # Thread messages under this text channel.
                async for msg in iter_text_channel_thread_messages(ch, after=after):
                    thread_id = int(getattr(getattr(msg, "channel", None), "id", 0) or 0) or None
                    e = _event_from_message(msg, parent_channel_id=ch.id, thread_id=thread_id)
                    if e:
                        events.append(e)

            elif isinstance(ch, discord.ForumChannel):
                async for msg in iter_forum_messages(ch, after=after):
                    thread_id = int(getattr(getattr(msg, "channel", None), "id", 0) or 0) or None
                    e = _event_from_message(msg, parent_channel_id=ch.id, thread_id=thread_id)
                    if e:
                        events.append(e)

        return events

    async def generate_and_send_report(
        self,
        channel: discord.abc.Messageable,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Generate metrics for the last N days and send a formatted report.

        Returns the JSON payload that is also written to disk.
        """

        if not self._client:
            raise RuntimeError("community_analytics agent not initialized")

        now_utc = now or datetime.now(timezone.utc)
        after = now_utc - timedelta(days=int(self.config.days))

        source_channels = self._resolve_source_channels()
        events = await self._collect_events(after=after, channels=source_channels)
        metrics = compute_metrics(events, tz=JST, top_n=5)

        previous_path = find_latest_weekly_report_path(self.config.data_dir)
        previous_payload = load_weekly_payload(previous_path) if previous_path else None

        integrations: dict[str, Any] = {
            "question_sla": load_question_sla_summary(),
            "membership": load_membership_summary(),
        }

        payload = build_weekly_payload(
            now=now_utc,
            after=after,
            days=int(self.config.days),
            metrics=metrics,
            previous_metrics=(previous_payload or {}).get("metrics") if previous_payload else None,
            integrations=integrations,
        )

        out_path = _write_weekly_payload(self.config.data_dir, payload, now=now_utc)
        lines = format_weekly_report(payload)
        await send_chunked(channel, lines)

        if self.config.debug:
            print(f"[community_analytics] wrote: {out_path}")
        return payload


def _event_from_message(
    msg: discord.Message,
    *,
    parent_channel_id: int,
    thread_id: int | None,
) -> AnalyticsEvent | None:
    author = getattr(msg, "author", None)
    if not author or getattr(author, "bot", False):
        return None

    created_at = getattr(msg, "created_at", None)
    if not isinstance(created_at, datetime):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    author_id = int(getattr(author, "id", 0) or 0)
    if not author_id:
        return None

    return AnalyticsEvent(
        created_at=created_at,
        author_id=author_id,
        channel_id=int(parent_channel_id),
        thread_id=int(thread_id) if thread_id is not None else None,
    )


def _write_weekly_payload(data_dir: Path, payload: dict[str, Any], *, now: datetime) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(JST).strftime("%Y%m%d")
    out_path = data_dir / f"weekly_{stamp}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
