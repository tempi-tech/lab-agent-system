import os
from src.core import config as core_config

# --- Configuration ---
APP_NAME = "discord_daily_summary"
USER_ID = "summary_user"
SESSION_ID = "summary_session"
GEMINI_MODEL = "gemini-2.0-flash-exp" # Updated to a widely available model or keep user's preference

# Discord Channel IDs
# random, tools (forum), topics (forum)
# TODO: Move these to environment variables or a config file for better flexibility
SOURCE_CHANNELS = [
    842348486234341407,  # random
    1436182005762097243, # tools
    1441407606395637993, # topics
]

# State Keys
STATE_TOPICS = "topics_summary"
STATE_HIGHLIGHT = "highlight_analysis"
STATE_LINKS = "link_summary"
STATE_FINAL_REPORT = "final_report"

# Reporter Settings
REPORTER_NAME = "ラボちゃん（研究生）"
# Relative path from this file
AVATAR_PATH = os.path.join(os.path.dirname(__file__), "assets", "lab-chan.jpeg")
