from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

import discord


def _parse_int_csv(value: str) -> List[int]:
    if not value:
        return []
    out: List[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


def _is_enabled(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no"}


@dataclass(frozen=True)
class DiscordAccessPolicy:
    enabled: bool
    allowed_channel_ids: List[int]
    require_mention: bool


def load_discord_access_policy() -> DiscordAccessPolicy:
    enabled = _is_enabled(os.getenv("DISCORD_ACCESS_ENABLED", "true"))
    allowed_channel_ids = _parse_int_csv(os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", ""))
    require_mention = os.getenv("DISCORD_REQUIRE_MENTION", "false").strip().lower() in {"1", "true", "yes"}
    return DiscordAccessPolicy(
        enabled=enabled,
        allowed_channel_ids=allowed_channel_ids,
        require_mention=require_mention,
    )


def is_message_allowed(
    message: discord.Message,
    policy: DiscordAccessPolicy,
    bot_user: discord.ClientUser | None,
) -> bool:
    if not policy.enabled:
        return True
    if policy.allowed_channel_ids and message.channel.id not in policy.allowed_channel_ids:
        return False
    if policy.require_mention:
        if not bot_user:
            return False
        if bot_user not in message.mentions:
            return False
    return True
