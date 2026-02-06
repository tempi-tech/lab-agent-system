from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _is_enabled(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no"}


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


def load_config() -> CommunityAnalyticsConfig:
    enabled = _is_enabled(os.getenv("COMMUNITY_ANALYTICS_ENABLED", "false"))
    channel_id = _get_int_env("COMMUNITY_ANALYTICS_CHANNEL_ID", 0)
    days = max(1, _get_int_env("COMMUNITY_ANALYTICS_DAYS", 7))
    data_dir = Path(os.getenv("COMMUNITY_ANALYTICS_DATA_DIR", "data/community_analytics"))
    debug = os.getenv("COMMUNITY_ANALYTICS_DEBUG", "false").strip().lower() in {"1", "true", "yes"}

    source_channel_ids = _parse_int_csv(os.getenv("SOURCE_CHANNEL_IDS", ""))
    source_category_ids = _parse_int_csv(os.getenv("SOURCE_CATEGORY_IDS", ""))
    source_channel_exclude_ids = set(_parse_int_csv(os.getenv("SOURCE_CHANNEL_EXCLUDE_IDS", "")))

    return CommunityAnalyticsConfig(
        enabled=enabled,
        channel_id=channel_id,
        days=days,
        data_dir=data_dir,
        debug=debug,
        source_channel_ids=source_channel_ids,
        source_category_ids=source_category_ids,
        source_channel_exclude_ids=source_channel_exclude_ids,
    )

