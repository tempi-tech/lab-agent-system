import discord
import os
from src.core import config
from src.core.bot import CommunityBot

# Import agents
# In a more advanced version, this could be dynamic import
from src.agents.daily_reporter import get_agent as get_daily_reporter

def main():
    if not config.DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not set. Please check your .env file.")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True # Needed for some agents to check roles/join date

    bot = CommunityBot(intents=intents)

    # Register Agents
    # You can conditionally register agents based on config here
    bot.register_agent(get_daily_reporter())

    bot.run(config.DISCORD_TOKEN)

if __name__ == "__main__":
    main()
