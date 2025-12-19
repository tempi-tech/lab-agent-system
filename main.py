import os
import sys
import asyncio
import discord
from dotenv import load_dotenv
from src.core.bot import CommunityBot
from src.core import config
# from src.agents.daily_reporter.logic import DailyReporterAgent
from src.agents.quiz_master import get_agent as get_quiz_master
from src.agents.invite_role_assigner.logic import InviteRoleAssignerAgent

# Load environment variables
load_dotenv()

def main():
    # Check for run-once mode (for GitHub Actions)
    run_once = "--once" in sys.argv

    # Initialize Bot
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True # Needed for member join events
    
    client = CommunityBot(intents=intents)

    # Register Agents
    # daily_reporter = DailyReporterAgent()
    # client.register_agent(daily_reporter)

    quiz_master = get_quiz_master()
    client.register_agent(quiz_master)

    client.register_agent(InviteRoleAssignerAgent())

    # Run Bot
    token = config.DISCORD_TOKEN
    if not token:
        print("Error: DISCORD_TOKEN not found in environment variables.")
        return

    if run_once:
        print("Starting in RUN-ONCE mode (GitHub Actions compatible)...")

        @client.event
        async def on_ready():
            print(f'Logged in as {client.user} (Run-Once Mode)')
            # # Manually trigger the daily report
            # target_channel_id = config.TARGET_CHANNEL_ID
            # target_channel = client.get_channel(int(target_channel_id)) if target_channel_id else None
            #
            # # Ensure agent has client reference
            # daily_reporter.client = client
            #
            # if target_channel:
            #     await daily_reporter.generate_summary(target_channel)
            # else:
            #     print(f"Error: Could not find target channel {target_channel_id}")

            print("Task completed. Closing connection.")
            await client.close()

    client.run(token)

if __name__ == "__main__":
    main()
