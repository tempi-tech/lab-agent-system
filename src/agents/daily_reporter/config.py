import os
from src.core import config as core_config

# --- Configuration ---
APP_NAME = "discord_daily_summary"
USER_ID = "summary_user"
SESSION_ID = "summary_session"
GEMINI_MODEL = "gemini-3-flash-preview"

# Discord Channel IDs
# Load from environment variable (comma-separated)
# Example: SOURCE_CHANNEL_IDS=123456789,987654321
_source_channels_str = os.environ.get("SOURCE_CHANNEL_IDS", "")
SOURCE_CHANNELS = [int(id.strip()) for id in _source_channels_str.split(",") if id.strip().isdigit()]

if not SOURCE_CHANNELS:
    print("Warning: SOURCE_CHANNEL_IDS not set or empty. No channels will be monitored.")

# State Keys
STATE_TOPICS = "topics_summary"
STATE_HIGHLIGHT = "highlight_analysis"
STATE_FINAL_REPORT = "final_report"

# Reporter Settings
REPORTER_NAME = "ラボちゃん（研究生）"
# Relative path from this file
AVATAR_PATH = os.path.join(os.path.dirname(__file__), "assets", "lab-chan.jpeg")
