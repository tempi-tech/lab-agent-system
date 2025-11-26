import discord
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.agent_base import BaseAgent

class CommunityBot(discord.Client):
    def __init__(self, intents: discord.Intents):
        super().__init__(intents=intents)
        self.agents = []

    def register_agent(self, agent_instance: 'BaseAgent'):
        """Registers an agent instance to the bot."""
        self.agents.append(agent_instance)
        print(f"Registered agent: {agent_instance.name}")

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
