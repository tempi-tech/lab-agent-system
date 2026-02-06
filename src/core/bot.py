import discord
import logging
from discord import app_commands
from typing import TYPE_CHECKING

from src.core.action_registry import ActionRegistry

if TYPE_CHECKING:
    from src.core.agent_base import BaseAgent

class CommunityBot(discord.Client):
    def __init__(self, intents: discord.Intents):
        super().__init__(intents=intents)
        self.agents: list["BaseAgent"] = []
        self.actions = ActionRegistry()
        self.tree = app_commands.CommandTree(self)

    def register_agent(self, agent_instance: 'BaseAgent'):
        """Registers an agent instance to the bot."""
        self.agents.append(agent_instance)
        print(f"Registered agent: {agent_instance.name}")
        if hasattr(agent_instance, "get_actions"):
            actions = agent_instance.get_actions()
            if actions:
                namespace = getattr(agent_instance, "action_namespace", agent_instance.name)
                self.actions.register(namespace, actions)

    async def on_ready(self):
        print(f'Logged in as {self.user}')
        print('--- Agents Starting ---')
        for agent in self.agents:
            if hasattr(agent, 'on_ready'):
                try:
                    await agent.on_ready(self)
                except Exception as e:
                    print(f"Error in {agent.__class__.__name__}.on_ready: {e}")
        print('--- Agents Started ---')

    async def on_message(self, message):
        if message.author.bot:
            return

        for agent in self.agents:
            if hasattr(agent, 'on_message'):
                try:
                    await agent.on_message(message)
                except Exception as e:
                    print(f"Error in {agent.__class__.__name__}.on_message: {e}")

    async def on_member_join(self, member):
        for agent in self.agents:
            if hasattr(agent, 'on_member_join'):
                try:
                    await agent.on_member_join(member)
                except Exception as e:
                    print(f"Error in {agent.__class__.__name__}.on_member_join: {e}")

    async def on_thread_create(self, thread):
        for agent in self.agents:
            if hasattr(agent, 'on_thread_create'):
                try:
                    await agent.on_thread_create(thread)
                except Exception as e:
                    print(f"Error in {agent.__class__.__name__}.on_thread_create: {e}")
