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
from src.agents.lab_onboarder import get_agent as get_lab_onboarder
from src.agents.membership_checker import get_agent as get_membership_checker
from src.agents.updates_assistant import get_agent as get_updates_assistant
from src.agents.claude_search import get_agent as get_claude_search

# Load environment variables
load_dotenv()

def main():
    # Check for run-once mode (for GitHub Actions)
    run_once = "--once" in sys.argv
    # Check for specific agent target (e.g., --once membership)
    run_once_target = None
    if run_once and len(sys.argv) > sys.argv.index("--once") + 1:
        run_once_target = sys.argv[sys.argv.index("--once") + 1]

    # Initialize Bot
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True # Needed for member join events

    client = CommunityBot(intents=intents)

    # Register Agents
    enable_daily = os.getenv("ENABLE_DAILY_REPORTER", "1").strip().lower() not in {"0", "false", "no"}
    daily_reporter = None
    membership_checker = None

    # Run-once mode: register only the target agent
    if run_once:
        if run_once_target == "membership":
            membership_checker = get_membership_checker()
            client.register_agent(membership_checker)
        else:
            # Default: daily_reporter
            if enable_daily:
                daily_reporter = DailyReporterAgent()
                client.register_agent(daily_reporter)

    if not run_once:
        enable_updates = os.getenv("UPDATES_ASSISTANT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        enable_claude_search = os.getenv("CLAUDE_SEARCH_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        quiz_master = get_quiz_master()
        client.register_agent(quiz_master)

        client.register_agent(InviteRoleAssignerAgent())
        client.register_agent(get_operator())
        client.register_agent(get_lab_onboarder())
        client.register_agent(get_membership_checker())
        if enable_updates:
            client.register_agent(get_updates_assistant())
        if enable_claude_search:
            client.register_agent(get_claude_search())

    # Run Bot
    token = config.DISCORD_TOKEN
    if not token:
        print("Error: DISCORD_TOKEN not found in environment variables.")
        return

    if run_once:
        print(f"Starting in RUN-ONCE mode (target: {run_once_target or 'daily_reporter'})...")
        run_once_channel_id = os.getenv("DISCORD_RUN_ONCE_CHANNEL_ID")
        if not run_once_channel_id:
            is_github_actions = os.getenv("GITHUB_ACTIONS", "").strip().lower() in {"1", "true", "yes"}
            run_once_channel_id = config.TARGET_CHANNEL_ID if is_github_actions else "1441302743229665422"

        @client.event
        async def on_ready():
            print(f'Logged in as {client.user} (Run-Once Mode)')

            if run_once_target == "membership" and membership_checker:
                # membership_checker の定期チェック実行
                await membership_checker.on_ready(client)
                await membership_checker.run_scheduled_check()
            elif daily_reporter:
                # daily_reporter の実行
                target_channel_id = run_once_channel_id
                target_channel = client.get_channel(int(target_channel_id)) if target_channel_id else None

                if target_channel:
                    daily_reporter.client = client
                    await daily_reporter.generate_summary(target_channel)
                else:
                    print(f"Error: Could not find target channel {target_channel_id}")
            else:
                print("Error: No agent configured for run-once mode")

            print("Task completed. Closing connection.")
            await client.close()

    client.run(token)

if __name__ == "__main__":
    main()
