from __future__ import annotations

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
