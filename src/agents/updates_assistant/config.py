import os
from dataclasses import dataclass
from typing import List


def _parse_int_csv(value: str) -> List[int]:
    if not value:
        return []
    out: List[int] = []
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


@dataclass(frozen=True)
class UpdatesAssistantConfig:
    enabled: bool
    allowed_channel_ids: List[int]
    rate_limit_seconds: int
    default_period: str
    llm_model: str
    debug: bool
    log_channel_id: int | None


def load_config() -> UpdatesAssistantConfig:
    enabled = _is_enabled(os.getenv("UPDATES_ASSISTANT_ENABLED", "true"))
    allowed_channel_ids = _parse_int_csv(os.getenv("UPDATES_ASSISTANT_ALLOWED_CHANNEL_IDS", ""))
    rate_limit_seconds = int(os.getenv("UPDATES_ASSISTANT_RATE_LIMIT_SECONDS", "30"))
    default_period = os.getenv("UPDATES_ASSISTANT_DEFAULT_PERIOD", "24h")
    llm_model = os.getenv("UPDATES_ASSISTANT_LLM_MODEL", "gemini-3-flash-preview")
    debug = os.getenv("UPDATES_ASSISTANT_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    log_channel_id_str = os.getenv("UPDATES_ASSISTANT_LOG_CHANNEL_ID", "")
    log_channel_id = int(log_channel_id_str) if log_channel_id_str.strip() else None

    return UpdatesAssistantConfig(
        enabled=enabled,
        allowed_channel_ids=allowed_channel_ids,
        rate_limit_seconds=rate_limit_seconds,
        default_period=default_period,
        llm_model=llm_model,
        debug=debug,
        log_channel_id=log_channel_id,
    )
