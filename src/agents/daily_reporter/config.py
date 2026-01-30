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
def _parse_id_list(raw: str) -> list[int]:
    return [int(token.strip()) for token in raw.split(",") if token.strip().isdigit()]


_source_channels_str = os.environ.get("SOURCE_CHANNEL_IDS", "")
SOURCE_CHANNELS = _parse_id_list(_source_channels_str)
SOURCE_CATEGORY_IDS = _parse_id_list(os.environ.get("SOURCE_CATEGORY_IDS", ""))
SOURCE_CHANNEL_EXCLUDE_IDS = set(_parse_id_list(os.environ.get("SOURCE_CHANNEL_EXCLUDE_IDS", "")))

if not SOURCE_CHANNELS and not SOURCE_CATEGORY_IDS:
    print("Warning: SOURCE_CHANNEL_IDS and SOURCE_CATEGORY_IDS not set or empty. No channels will be monitored.")

# State Keys
STATE_TOPICS = "topics_summary"
STATE_HIGHLIGHT = "highlight_analysis"
STATE_TIPS = "tips_analysis"
STATE_FINAL_REPORT = "final_report"

# Reporter Settings
REPORTER_NAME = "ラボちゃん（研究生）"
# Relative path from this file
AVATAR_PATH = os.path.join(os.path.dirname(__file__), "assets", "lab-chan.jpeg")


def _get_bool_env(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _get_int_env(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float_env(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Radio (audio) settings
RADIO_ENABLED = _get_bool_env("DAILY_REPORT_AUDIO_ENABLED", False)
RADIO_DRY_RUN = _get_bool_env("DAILY_REPORT_AUDIO_DRY_RUN", False)
RADIO_TTS_MODEL = os.environ.get("DAILY_REPORT_TTS_MODEL", "gemini-2.5-pro-preview-tts")
RADIO_TTS_TEMPERATURE = _get_float_env("DAILY_REPORT_TTS_TEMPERATURE", 0.4)
RADIO_MAX_TOPICS = _get_int_env("DAILY_REPORT_RADIO_MAX_TOPICS", 4)
RADIO_TARGET_MINUTES = _get_int_env("DAILY_REPORT_RADIO_TARGET_MINUTES", 10)
RADIO_MAX_CHARS = _get_int_env("DAILY_REPORT_RADIO_MAX_CHARS", 1600)
RADIO_VOICE_LABCHAN = os.environ.get("DAILY_REPORT_RADIO_VOICE_LABCHAN", "Sulafat")
RADIO_VOICE_YUKI = os.environ.get("DAILY_REPORT_RADIO_VOICE_YUKI", "Puck")
RADIO_BASE_DIR = os.environ.get("DAILY_REPORT_AUDIO_DIR", "data/radio")
RADIO_SINGLE_PASS = _get_bool_env("DAILY_REPORT_RADIO_SINGLE_PASS", True)
RADIO_MAX_UPLOAD_BYTES = _get_int_env("DAILY_REPORT_RADIO_MAX_UPLOAD_BYTES", 8_000_000)
RADIO_MP3_BITRATE = os.environ.get("DAILY_REPORT_RADIO_MP3_BITRATE", "96k")
RADIO_KNOWLEDGE_PATH = os.environ.get(
    "DAILY_REPORT_RADIO_KNOWLEDGE_PATH",
    os.path.join(os.path.dirname(__file__), "radio_knowledge.md"),
)

# State keys
STATE_RADIO_SCRIPT = "radio_script"
