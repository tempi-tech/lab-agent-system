from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord

from src.core.agent_base import BaseAgent
from src.core.discord_text import send_chunked

from .config import QuestionSLAConfig, load_config
from . import storage


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class QuestionSlaAgent(BaseAgent):
    def __init__(self) -> None:
        self.config: QuestionSLAConfig = load_config()
        self._client: discord.Client | None = None
        self._tick_task: asyncio.Task[None] | None = None

        self.action_namespace = "question_sla"

    @property
    def name(self) -> str:
        return "question_sla"

    def get_actions(self):
        return {"report": self.action_report, "close": self.action_close}

    async def on_ready(self, client: discord.Client) -> None:
        self._client = client
        if not self.config.enabled:
            print("[question_sla] disabled")
            return
        if not self.config.forum_channel_ids:
            print("[question_sla] enabled but QUESTION_SLA_FORUM_CHANNEL_IDS is empty; nothing to monitor")
            return

        storage.ensure_schema(self.config.sqlite_path)
        print(
            f"[question_sla] ready forum_ids={self.config.forum_channel_ids} "
            f"escalation_channel={self.config.escalation_channel_id or 'NONE'}"
        )

        if self.config.tick_seconds > 0:
            self._tick_task = asyncio.create_task(self._tick_loop())

    async def action_report(self, message: discord.Message, args: list[str]) -> None:
        if not self.config.enabled:
            await message.channel.send("question_sla is disabled.")
            return
        target = message.channel
        if args and args[0].lower() != "here" and self.config.escalation_channel_id and self._client:
            ch = self._client.get_channel(self.config.escalation_channel_id)
            if ch and hasattr(ch, "send"):
                target = ch  # type: ignore[assignment]

        now = datetime.now(timezone.utc)
        rows = storage.list_questions(self.config.sqlite_path, status="open", limit=100)
        lines: list[str] = []
        lines.append("📌 未回答質問SLAレポート")
        lines.append(f"- open: {len(rows)}")
        lines.append("")
        for row in rows:
            age = _age_minutes(row.created_at, now)
            url = f"https://discord.com/channels/{row.guild_id}/{row.thread_id}/{row.starter_message_id}"
            lines.append(f"- {age}m <@{row.starter_author_id}> {url}")

        await send_chunked(target, lines)

    async def action_close(self, message: discord.Message, args: list[str]) -> None:
        if not args:
            await message.channel.send("Usage: `!agent question_sla close <jump_url|message_id>`")
            return
        starter_id = _parse_starter_message_id(args[0])
        if not starter_id:
            await message.channel.send("Could not parse message id.")
            return
        storage.mark_closed(self.config.sqlite_path, starter_id)
        await message.channel.send(f"✅ closed: {starter_id}")

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._run_tick_once()
            except Exception as exc:
                if self.config.debug:
                    print(f"[question_sla] tick error: {exc!r}")
            await asyncio.sleep(max(1, int(self.config.tick_seconds)))

    async def _run_tick_once(self, *, now: datetime | None = None) -> None:
        if not self._client:
            return
        if not self.config.escalation_channel_id:
            return
        channel = self._client.get_channel(self.config.escalation_channel_id)
        send_fn = getattr(channel, "send", None)
        if not callable(send_fn):
            return

        now_dt = now or datetime.now(timezone.utc)
        open_rows = storage.list_questions(self.config.sqlite_path, status="open", limit=500)
        for row in open_rows:
            try:
                desired_stage = _desired_stage_minutes(
                    created_at_iso=row.created_at,
                    now=now_dt,
                    first_reminder_minutes=self.config.first_reminder_minutes,
                    escalate_minutes=self.config.escalate_minutes,
                )
            except Exception:
                continue

            # Best-effort catch-up for restarts: before notifying, scan history.
            if desired_stage >= 1 and int(row.reminded_stage) == 0:
                try:
                    first_response_at = await self._scan_first_response(row)
                except Exception as exc:
                    first_response_at = None
                    if self.config.debug:
                        print(f"[question_sla] scan error: {exc!r}")
                if first_response_at:
                    storage.mark_answered(
                        self.config.sqlite_path,
                        int(row.starter_message_id),
                        _iso(first_response_at),
                    )
                    continue
            if desired_stage <= int(row.reminded_stage):
                continue
            # Send only the next stage notification.
            stage_to_send = int(row.reminded_stage) + 1 if desired_stage > int(row.reminded_stage) else desired_stage
            if stage_to_send == 1:
                await send_fn(self._format_reminder(row, now_dt))
            elif stage_to_send >= 2:
                await send_fn(self._format_escalation(row, now_dt))
                stage_to_send = 2

            try:
                storage.update_notification_state(
                    self.config.sqlite_path,
                    int(row.starter_message_id),
                    reminded_stage=int(stage_to_send),
                    last_notified_at_iso=_iso(now_dt),
                )
            except Exception as exc:
                if self.config.debug:
                    print(f"[question_sla] update_notification_state error: {exc!r}")

    async def _scan_first_response(self, row: storage.QuestionRow) -> datetime | None:
        if not self._client:
            return None
        created_at = _parse_iso(row.created_at)
        ch = self._client.get_channel(int(row.thread_id))
        thread = ch if isinstance(ch, discord.Thread) else None
        if not thread:
            return None
        return await _scan_thread_for_first_response(
            thread,
            starter_author_id=int(row.starter_author_id),
            after=created_at,
        )

    async def on_message(self, message: discord.Message) -> None:
        if not self.config.enabled:
            return
        if not message.guild:
            return
        if not isinstance(message.channel, discord.Thread):
            return
        thread: discord.Thread = message.channel
        if thread.parent_id not in self.config.forum_channel_ids:
            return

        row = storage.get_question(self.config.sqlite_path, int(thread.id))
        if not row:
            return
        if row.status != "open":
            return
        if int(getattr(message.author, "id", 0) or 0) == int(row.starter_author_id):
            return

        created_at = getattr(message, "created_at", None)
        if not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)
        storage.mark_answered(self.config.sqlite_path, int(row.starter_message_id), _iso(created_at))
        if self.config.debug:
            print(f"[question_sla] answered starter={row.starter_message_id} by={message.author.id}")

    async def on_thread_create(self, thread: discord.Thread) -> None:
        if not self.config.enabled:
            return
        parent_id = getattr(thread, "parent_id", None)
        if not parent_id or parent_id not in self.config.forum_channel_ids:
            return
        # Forum posts are threads; treat the thread itself as the \"question\" starter.
        # For forum posts, thread.owner_id should point to the author.
        owner_id = getattr(thread, "owner_id", None)
        if not owner_id:
            return
        created_at = getattr(thread, "created_at", None)
        created_at_iso = _iso(created_at) if isinstance(created_at, datetime) else _iso(datetime.now(timezone.utc))

        storage.upsert_open_question(
            self.config.sqlite_path,
            guild_id=int(getattr(thread.guild, "id", 0) or 0),
            thread_id=int(thread.id),
            starter_message_id=int(thread.id),
            starter_author_id=int(owner_id),
            created_at_iso=created_at_iso,
        )
        if self.config.debug:
            print(f"[question_sla] tracked thread_id={thread.id} owner_id={owner_id}")

    def _format_reminder(self, row: storage.QuestionRow, now: datetime) -> str:
        age_minutes = _age_minutes(row.created_at, now)
        url = f"https://discord.com/channels/{row.guild_id}/{row.thread_id}/{row.starter_message_id}"
        return (
            f"⏰ 未回答の質問が {age_minutes}分 経過しています\n"
            f"- Author: <@{row.starter_author_id}>\n"
            f"- URL: {url}"
        )

    def _format_escalation(self, row: storage.QuestionRow, now: datetime) -> str:
        age_minutes = _age_minutes(row.created_at, now)
        url = f"https://discord.com/channels/{row.guild_id}/{row.thread_id}/{row.starter_message_id}"
        return (
            f"🚨 未回答の質問が {age_minutes}分 経過しています (エスカレーション)\n"
            f"- Author: <@{row.starter_author_id}>\n"
            f"- URL: {url}"
        )


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_minutes(created_at_iso: str, now: datetime) -> int:
    created_at = _parse_iso(created_at_iso)
    delta = now.astimezone(timezone.utc) - created_at
    return max(0, int(delta.total_seconds() // 60))


def _desired_stage_minutes(
    *,
    created_at_iso: str,
    now: datetime,
    first_reminder_minutes: int,
    escalate_minutes: int,
) -> int:
    minutes = _age_minutes(created_at_iso, now)
    if minutes >= int(escalate_minutes):
        return 2
    if minutes >= int(first_reminder_minutes):
        return 1
    return 0


def _parse_starter_message_id(raw: str) -> int | None:
    token = raw.strip()
    if token.isdigit():
        try:
            return int(token)
        except ValueError:
            return None
    if token.startswith("https://discord.com/channels/"):
        parts = token.split("/")
        # .../<guild>/<channel>/<message>
        if parts and parts[-1].isdigit():
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


async def _scan_thread_for_first_response(
    thread: discord.Thread,
    *,
    starter_author_id: int,
    after: datetime,
    limit: int = 50,
) -> datetime | None:
    async for message in thread.history(after=after, limit=limit, oldest_first=True):
        author = getattr(message, "author", None)
        author_id = int(getattr(author, "id", 0) or 0)
        if author_id and author_id != int(starter_author_id):
            created_at = getattr(message, "created_at", None)
            if isinstance(created_at, datetime):
                return created_at
    return None
