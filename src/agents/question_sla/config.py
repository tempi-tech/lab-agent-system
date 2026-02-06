from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class QuestionSLAConfig:
    enabled: bool
    forum_channel_ids: list[int]
    escalation_channel_id: int
    first_reminder_minutes: int
    escalate_minutes: int
    tick_seconds: int
    sqlite_path: Path
    debug: bool


def load_config() -> QuestionSLAConfig:
    enabled = _is_enabled(os.getenv("QUESTION_SLA_ENABLED", "false"))
    forum_channel_ids = _parse_int_csv(os.getenv("QUESTION_SLA_FORUM_CHANNEL_IDS", ""))
    escalation_channel_id = _get_int_env("QUESTION_SLA_ESCALATION_CHANNEL_ID", 0)
    first_reminder_minutes = _get_int_env("QUESTION_SLA_FIRST_REMINDER_MINUTES", 120)
    escalate_minutes = _get_int_env("QUESTION_SLA_ESCALATE_MINUTES", 1440)
    tick_seconds = _get_int_env("QUESTION_SLA_TICK_SECONDS", 60)
    sqlite_path = Path(os.getenv("QUESTION_SLA_SQLITE_PATH", "data/question_sla/state.sqlite"))
    debug = os.getenv("QUESTION_SLA_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    return QuestionSLAConfig(
        enabled=enabled,
        forum_channel_ids=forum_channel_ids,
        escalation_channel_id=escalation_channel_id,
        first_reminder_minutes=first_reminder_minutes,
        escalate_minutes=escalate_minutes,
        tick_seconds=tick_seconds,
        sqlite_path=sqlite_path,
        debug=debug,
    )

