import os
import sys
import asyncio
import discord
from dotenv import load_dotenv
from src.core.bot import CommunityBot
from src.core import config
from src.agents.daily_reporter.logic import DailyReporterAgent
from src.agents.quiz_master import get_agent as get_quiz_master
from src.agents.invite_role_assigner.logic import InviteRoleAssignerAgent
from src.agents.operator import get_agent as get_operator

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
    enable_daily = os.getenv("ENABLE_DAILY_REPORTER", "1").strip().lower() not in {"0", "false", "no"}
    daily_reporter = None
    if run_once or enable_daily:
        daily_reporter = DailyReporterAgent()
        client.register_agent(daily_reporter)

    if not run_once:
        quiz_master = get_quiz_master()
        client.register_agent(quiz_master)

        client.register_agent(InviteRoleAssignerAgent())
        client.register_agent(get_operator())

    # Run Bot
    token = config.DISCORD_TOKEN
    if not token:
        print("Error: DISCORD_TOKEN not found in environment variables.")
        return

    if run_once:
        print("Starting in RUN-ONCE mode (GitHub Actions compatible)...")
        run_once_channel_id = os.getenv("DISCORD_RUN_ONCE_CHANNEL_ID")
        if not run_once_channel_id:
            is_github_actions = os.getenv("GITHUB_ACTIONS", "").strip().lower() in {"1", "true", "yes"}
            run_once_channel_id = config.TARGET_CHANNEL_ID if is_github_actions else "1441302743229665422"

        @client.event
        async def on_ready():
            print(f'Logged in as {client.user} (Run-Once Mode)')
            target_channel_id = run_once_channel_id
            target_channel = client.get_channel(int(target_channel_id)) if target_channel_id else None

            if target_channel and daily_reporter:
                daily_reporter.client = client
                await daily_reporter.generate_summary(target_channel)
            else:
                print(f"Error: Could not find target channel {target_channel_id}")

            print("Task completed. Closing connection.")
            await client.close()

    client.run(token)

if __name__ == "__main__":
    main()
