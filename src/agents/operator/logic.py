from __future__ import annotations

import os
from typing import List, Optional

import discord

from src.core.agent_base import BaseAgent
from src.core.action_registry import ActionRegistry


class OperatorAgent(BaseAgent):
    """
    Simple command router that calls registered agent actions.

    Usage:
      !agent <namespace> <action> [args...]
    Example:
      !agent daily_reporter run
    """

    def __init__(self) -> None:
        self._actions: Optional[ActionRegistry] = None

    @staticmethod
    def _parse_int_csv(value: str) -> set[int]:
        out: set[int] = set()
        for part in value.split(","):
            token = part.strip()
            if not token:
                continue
            try:
                out.add(int(token))
            except ValueError:
                continue
        return out

    def _is_authorized(self, message: discord.Message) -> bool:
        admin_user_ids = self._parse_int_csv(os.getenv("OPERATOR_ADMIN_USER_IDS", ""))
        admin_role_ids = self._parse_int_csv(os.getenv("OPERATOR_ADMIN_ROLE_IDS", ""))
        allowed_channel_ids = self._parse_int_csv(
            os.getenv("OPERATOR_ALLOWED_CHANNEL_IDS", "")
        )

        if allowed_channel_ids and message.channel.id not in allowed_channel_ids:
            return False

        if message.author.id in admin_user_ids:
            return True

        member = getattr(message, "author", None)
        if isinstance(member, discord.Member):
            if member.guild_permissions.administrator:
                return True
            if admin_role_ids and any(
                role.id in admin_role_ids for role in member.roles
            ):
                return True

        return False

    @property
    def name(self) -> str:
        return "operator"

    async def on_ready(self, client: discord.Client) -> None:
        self._actions = client.actions
        print("OperatorAgent is ready.")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self._actions:
            return

        content = message.content.strip()
        if not content.startswith("!agent"):
            return

        if not self._is_authorized(message):
            await message.channel.send(
                "You are not authorized to run operator actions."
            )
            return

        parts = content.split()
        if len(parts) < 3:
            await message.channel.send(
                "Usage: `!agent <namespace> <action> [args...]`\n"
                f"Available: {', '.join(self._actions.list()) or '(none)'}"
            )
            return

        namespace, action = parts[1], parts[2]
        args = parts[3:]
        key = f"{namespace}.{action}"

        fn = self._actions.get(key)
        if not fn:
            await message.channel.send(
                f"Unknown action `{key}`. Available: {', '.join(self._actions.list()) or '(none)'}"
            )
            return

        await fn(message, args)
