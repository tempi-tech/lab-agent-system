from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _is_enabled(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no"}

def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_int_csv(value: str) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class CommunityAnalyticsConfig:
    enabled: bool
    channel_id: int
    days: int
    data_dir: Path
    debug: bool
    source_channel_ids: list[int]
    source_category_ids: list[int]
    source_channel_exclude_ids: set[int]
    dashboard_enabled: bool
    dashboard_update_seconds: int
    dashboard_message_id: int
    dashboard_pin: bool
    dashboard_state_path: Path


def load_config() -> CommunityAnalyticsConfig:
    enabled = _is_enabled(os.getenv("COMMUNITY_ANALYTICS_ENABLED", "false"))
    channel_id = _get_int_env("COMMUNITY_ANALYTICS_CHANNEL_ID", 0)
    days = max(1, _get_int_env("COMMUNITY_ANALYTICS_DAYS", 7))
    data_dir = Path(os.getenv("COMMUNITY_ANALYTICS_DATA_DIR", "data/community_analytics"))
    debug = os.getenv("COMMUNITY_ANALYTICS_DEBUG", "false").strip().lower() in {"1", "true", "yes"}

    source_channel_ids = _parse_int_csv(os.getenv("SOURCE_CHANNEL_IDS", ""))
    source_category_ids = _parse_int_csv(os.getenv("SOURCE_CATEGORY_IDS", ""))
    source_channel_exclude_ids = set(_parse_int_csv(os.getenv("SOURCE_CHANNEL_EXCLUDE_IDS", "")))

    dashboard_enabled = _is_enabled(os.getenv("COMMUNITY_ANALYTICS_DASHBOARD_ENABLED", "false"))
    dashboard_update_seconds = max(
        60,
        _get_int_env("COMMUNITY_ANALYTICS_DASHBOARD_UPDATE_SECONDS", 3600),
    )
    dashboard_message_id = _get_int_env("COMMUNITY_ANALYTICS_DASHBOARD_MESSAGE_ID", 0)
    dashboard_pin = _get_bool_env("COMMUNITY_ANALYTICS_DASHBOARD_PIN", False)

    dashboard_state_path = Path(
        os.getenv(
            "COMMUNITY_ANALYTICS_DASHBOARD_STATE_PATH",
            str(data_dir / "dashboard_state.json"),
        )
    )

    return CommunityAnalyticsConfig(
        enabled=enabled,
        channel_id=channel_id,
        days=days,
        data_dir=data_dir,
        debug=debug,
        source_channel_ids=source_channel_ids,
        source_category_ids=source_category_ids,
        source_channel_exclude_ids=source_channel_exclude_ids,
        dashboard_enabled=dashboard_enabled,
        dashboard_update_seconds=dashboard_update_seconds,
        dashboard_message_id=dashboard_message_id,
        dashboard_pin=dashboard_pin,
        dashboard_state_path=dashboard_state_path,
    )
