from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

import discord


async def iter_text_channel_messages(
    channel: discord.TextChannel,
    *,
    after: datetime,
) -> AsyncIterator[discord.Message]:
    async for message in channel.history(after=after, limit=None):
        yield message


async def iter_forum_messages(
    channel: discord.ForumChannel,
    *,
    after: datetime,
) -> AsyncIterator[discord.Message]:
    # Forum: active threads + archived threads.
    seen_thread_ids: set[int] = set()

    for thread in getattr(channel, "threads", []) or []:
        if getattr(thread, "id", None) in seen_thread_ids:
            continue
        seen_thread_ids.add(thread.id)
        async for message in thread.history(after=after, limit=None):
            yield message

    async for thread in channel.archived_threads(limit=None):
        if getattr(thread, "id", None) in seen_thread_ids:
            continue
        # archived_threads returns newest-first; stop once older than the window.
        ts = getattr(thread, "archive_timestamp", None)
        if ts and ts < after:
            break
        seen_thread_ids.add(thread.id)
        async for message in thread.history(after=after, limit=None):
            yield message


async def iter_text_channel_thread_messages(
    channel: discord.TextChannel,
    *,
    after: datetime,
    include_private_threads: bool = False,
) -> AsyncIterator[discord.Message]:
    # Text channel: active threads + archived (public/private).
    seen_thread_ids: set[int] = set()

    for thread in getattr(channel, "threads", []) or []:
        if getattr(thread, "id", None) in seen_thread_ids:
            continue
        seen_thread_ids.add(thread.id)
        async for message in thread.history(after=after, limit=None):
            yield message

    async for thread in channel.archived_threads(limit=None):
        if getattr(thread, "id", None) in seen_thread_ids:
            continue
        ts = getattr(thread, "archive_timestamp", None)
        if ts and ts < after:
            break
        seen_thread_ids.add(thread.id)
        async for message in thread.history(after=after, limit=None):
            yield message

    if include_private_threads:
        try:
            async for thread in channel.archived_threads(limit=None, private=True):
                if getattr(thread, "id", None) in seen_thread_ids:
                    continue
                ts = getattr(thread, "archive_timestamp", None)
                if ts and ts < after:
                    break
                seen_thread_ids.add(thread.id)
                async for message in thread.history(after=after, limit=None):
                    yield message
        except discord.Forbidden:
            return

