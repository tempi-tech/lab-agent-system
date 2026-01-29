from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, List

import httpx


@dataclass(frozen=True)
class DiscordSearchResult:
    content: str
    channel_id: int
    channel_name: str
    author_id: int
    author_name: str
    message_id: int
    created_at: str
    jump_url: str


def build_search_params(
    *,
    query: str,
    channel_ids: Iterable[int] | None,
    author_ids: Iterable[int] | None,
    limit: int,
) -> List[tuple[str, str]]:
    params: List[tuple[str, str]] = [("content", query)]
    if channel_ids:
        for channel_id in channel_ids:
            params.append(("channel_id", str(channel_id)))
    if author_ids:
        for author_id in author_ids:
            params.append(("author_id", str(author_id)))
    clamped = max(1, min(int(limit), 25))
    params.append(("limit", str(clamped)))
    return params


async def search_messages_discord(
    *,
    guild_id: int,
    query: str,
    bot_token: str,
    channel_ids: Iterable[int] | None = None,
    author_ids: Iterable[int] | None = None,
    limit: int = 25,
    resolve_channel_name: Callable[[int], str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> List[DiscordSearchResult] | None:
    if not bot_token:
        return None

    params = build_search_params(
        query=query,
        channel_ids=channel_ids,
        author_ids=author_ids,
        limit=limit,
    )
    url = f"https://discord.com/api/v10/guilds/{guild_id}/messages/search"
    headers = {"authorization": f"Bot {bot_token}"}

    try:
        async with httpx.AsyncClient(timeout=20, transport=transport) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.RequestError:
        return None

    if resp.status_code == 429:
        return None
    if resp.status_code >= 400:
        return None

    data = resp.json()
    raw_messages = data.get("messages") or []
    results: List[DiscordSearchResult] = []
    for group in raw_messages:
        if not isinstance(group, list) or not group:
            continue
        msg = group[0]
        if not isinstance(msg, dict):
            continue
        if msg.get("type") not in (0, None):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        channel_id = int(msg.get("channel_id", 0) or 0)
        message_id = int(msg.get("id", 0) or 0)
        author = msg.get("author") or {}
        author_id = int(author.get("id", 0) or 0)
        author_name = author.get("global_name") or author.get("username") or "unknown"
        created_at = msg.get("timestamp") or datetime.utcnow().isoformat()
        channel_name = resolve_channel_name(channel_id) if resolve_channel_name else "unknown"
        jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        results.append(
            DiscordSearchResult(
                content=content,
                channel_id=channel_id,
                channel_name=channel_name,
                author_id=author_id,
                author_name=author_name,
                message_id=message_id,
                created_at=created_at,
                jump_url=jump_url,
            )
        )

    return results
