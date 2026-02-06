from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Sequence

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
        self._dashboard_task: asyncio.Task[None] | None = None

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
        self._maybe_start_dashboard()

    def _maybe_start_dashboard(self) -> None:
        if not self.config.enabled or not self.config.dashboard_enabled:
            return
        if self._dashboard_task and not self._dashboard_task.done():
            return
        self._dashboard_task = asyncio.create_task(self._dashboard_loop())

    async def run_scheduled_report(self, *, target_channel_id: int | None = None) -> None:
        """
        Run a single analytics report (intended for `python main.py --once analytics`).

        If `target_channel_id` is provided, it overrides config.channel_id.
        """

        if not self._client:
            print("[community_analytics] not initialized")
            return

        channel_id = (
            int(target_channel_id or 0)
            or int(self.config.channel_id or 0)
            or _get_int_env("DISCORD_RUN_ONCE_CHANNEL_ID")
        )
        if not channel_id:
            print("[community_analytics] COMMUNITY_ANALYTICS_CHANNEL_ID not set")
            return

        ch = self._client.get_channel(channel_id)
        if not isinstance(ch, discord.abc.Messageable):
            print(f"[community_analytics] channel not found/messageable: {channel_id}")
            return

        await self.generate_and_send_report(ch)

    async def _dashboard_loop(self) -> None:
        # Update immediately, then on a fixed interval.
        while True:
            try:
                await self.update_dashboard()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.config.debug:
                    print(f"[community_analytics] dashboard update failed: {exc!r}")
            await asyncio.sleep(int(self.config.dashboard_update_seconds))

    async def update_dashboard(self) -> None:
        """
        Update (edit-in-place) a single dashboard message in the configured channel.

        - If `COMMUNITY_ANALYTICS_DASHBOARD_MESSAGE_ID` is set: edit that message only.
        - Else: create a new message if needed and persist its message_id under
          `COMMUNITY_ANALYTICS_DASHBOARD_STATE_PATH` (gitignored path under data/ by default).
        """
        if not self._client:
            return
        if not self.config.enabled or not self.config.dashboard_enabled:
            return

        channel_id = int(self.config.channel_id or 0)
        if not channel_id:
            if self.config.debug:
                print("[community_analytics] dashboard: COMMUNITY_ANALYTICS_CHANNEL_ID not set")
            return

        ch = self._client.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self._client.fetch_channel(channel_id)
            except Exception:
                if self.config.debug:
                    print(f"[community_analytics] dashboard: failed to fetch channel: {channel_id}")
                return
        if not isinstance(ch, discord.abc.Messageable):
            if self.config.debug:
                print(f"[community_analytics] dashboard: channel not messageable: {channel_id}")
            return

        forced_message_id = int(self.config.dashboard_message_id or 0)
        state_path = self.config.dashboard_state_path
        state = _load_dashboard_state(state_path)

        message_id: int | None = None
        if forced_message_id:
            message_id = forced_message_id
        elif state and state.channel_id == channel_id and state.message_id:
            message_id = state.message_id

        msg: discord.Message | None = None
        if message_id:
            try:
                msg = await ch.fetch_message(int(message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                msg = None

        payload, out_path = await self.generate_weekly_payload(now=None)
        text = _render_dashboard_text(payload, limit=1900)

        if msg is None:
            if forced_message_id:
                # If the operator forced a message id and we can't fetch/edit it, don't spam new messages.
                if self.config.debug:
                    print(
                        f"[community_analytics] dashboard: forced message not found/editable: {forced_message_id}"
                    )
                return

            msg = await ch.send(text)
            if self.config.dashboard_pin:
                try:
                    await msg.pin(reason="Community analytics dashboard")
                except Exception:
                    pass
            _save_dashboard_state(state_path, DashboardState(channel_id=channel_id, message_id=int(msg.id)))
        else:
            try:
                await msg.edit(content=text)
            except (discord.Forbidden, discord.HTTPException):
                if self.config.debug:
                    print("[community_analytics] dashboard: failed to edit message")

        if self.config.debug:
            print(f"[community_analytics] dashboard updated: channel={channel_id} message={msg.id} out={out_path}")

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
    ) -> tuple[list[AnalyticsEvent], list[str]]:
        events: list[AnalyticsEvent] = []
        errors: list[str] = []

        for ch in channels:
            if isinstance(ch, discord.TextChannel):
                # Parent channel messages.
                try:
                    async for msg in iter_text_channel_messages(ch, after=after):
                        e = _event_from_message(msg, parent_channel_id=ch.id, thread_id=None)
                        if e:
                            events.append(e)
                except Exception as exc:
                    errors.append(f"text_history_failed channel_id={ch.id} exc={exc.__class__.__name__}")

                # Thread messages under this text channel.
                try:
                    async for msg in iter_text_channel_thread_messages(ch, after=after):
                        thread_id = int(getattr(getattr(msg, "channel", None), "id", 0) or 0) or None
                        e = _event_from_message(msg, parent_channel_id=ch.id, thread_id=thread_id)
                        if e:
                            events.append(e)
                except Exception as exc:
                    errors.append(f"text_threads_failed channel_id={ch.id} exc={exc.__class__.__name__}")

            elif isinstance(ch, discord.ForumChannel):
                try:
                    async for msg in iter_forum_messages(ch, after=after):
                        thread_id = int(getattr(getattr(msg, "channel", None), "id", 0) or 0) or None
                        e = _event_from_message(msg, parent_channel_id=ch.id, thread_id=thread_id)
                        if e:
                            events.append(e)
                except Exception as exc:
                    errors.append(f"forum_failed channel_id={ch.id} exc={exc.__class__.__name__}")

        return events, errors

    async def generate_weekly_payload(
        self,
        *,
        now: datetime | None,
    ) -> tuple[dict[str, Any], Path]:
        if not self._client:
            raise RuntimeError("community_analytics agent not initialized")

        now_utc = now or datetime.now(timezone.utc)
        after = now_utc - timedelta(days=int(self.config.days))

        source_channels = self._resolve_source_channels()
        events, errors = await self._collect_events(after=after, channels=source_channels)
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
        if errors:
            payload["errors"] = list(errors)

        out_path = _write_weekly_payload(self.config.data_dir, payload, now=now_utc)
        return payload, out_path

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

        payload, out_path = await self.generate_weekly_payload(now=now)
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


def _get_int_env(name: str) -> int:
    raw = (os.getenv(name) or "").strip()
    if raw.isdigit():
        return int(raw)
    return 0


class DashboardState:
    def __init__(self, *, channel_id: int, message_id: int) -> None:
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)


def _load_dashboard_state(path: Path) -> DashboardState | None:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        channel_id = int(raw.get("channel_id", 0) or 0)
        message_id = int(raw.get("message_id", 0) or 0)
        if not channel_id or not message_id:
            return None
        return DashboardState(channel_id=channel_id, message_id=message_id)
    except Exception:
        return None


def _save_dashboard_state(path: Path, state: DashboardState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"channel_id": state.channel_id, "message_id": state.message_id},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _render_dashboard_text(payload: dict[str, Any], *, limit: int) -> str:
    text = "\n".join(format_weekly_report(payload))
    if len(text) <= limit:
        return text
    suffix = "\n...(truncated)"
    return text[: max(0, limit - len(suffix))] + suffix
