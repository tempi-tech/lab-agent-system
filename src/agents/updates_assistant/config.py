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
    llm_provider: str
    llm_model: str
    reaction_emoji: str
    chat_only: bool
    context_max_messages: int
    context_include_bots: bool
    reply_as_mention: bool
    router_enabled: bool
    router_llm_provider: str
    router_llm_model: str
    router_llm_temperature: float
    router_llm_max_output_tokens: int
    router_default_scope: str
    debug: bool
    log_channel_id: int | None


def load_config() -> UpdatesAssistantConfig:
    enabled = _is_enabled(os.getenv("UPDATES_ASSISTANT_ENABLED", "true"))
    allowed_channel_ids = _parse_int_csv(os.getenv("UPDATES_ASSISTANT_ALLOWED_CHANNEL_IDS", ""))
    rate_limit_seconds = int(os.getenv("UPDATES_ASSISTANT_RATE_LIMIT_SECONDS", "30"))
    default_period = os.getenv("UPDATES_ASSISTANT_DEFAULT_PERIOD", "24h")
    llm_provider = os.getenv("UPDATES_ASSISTANT_LLM_PROVIDER", "openrouter").strip()
    llm_model = os.getenv("UPDATES_ASSISTANT_LLM_MODEL", "deepseek/deepseek-v3.2").strip()
    reaction_emoji = os.getenv("UPDATES_ASSISTANT_REACTION_EMOJI", "👀").strip()
    chat_only = _is_enabled(os.getenv("UPDATES_ASSISTANT_CHAT_ONLY", "false"))
    context_max_messages = int(os.getenv("UPDATES_ASSISTANT_CONTEXT_MAX_MESSAGES", "15"))
    context_include_bots = _is_enabled(os.getenv("UPDATES_ASSISTANT_CONTEXT_INCLUDE_BOTS", "false"))
    reply_as_mention = _is_enabled(os.getenv("UPDATES_ASSISTANT_REPLY_AS_MENTION", "true"))
    router_enabled = _is_enabled(os.getenv("UPDATES_ASSISTANT_ROUTER_ENABLED", "true"))
    router_llm_provider = os.getenv("UPDATES_ASSISTANT_ROUTER_LLM_PROVIDER", "openrouter").strip()
    router_llm_model = os.getenv("UPDATES_ASSISTANT_ROUTER_LLM_MODEL", "deepseek/deepseek-v3.2").strip()
    router_llm_temperature = float(os.getenv("UPDATES_ASSISTANT_ROUTER_LLM_TEMPERATURE", "0.0"))
    router_llm_max_output_tokens = int(os.getenv("UPDATES_ASSISTANT_ROUTER_LLM_MAX_OUTPUT_TOKENS", "256"))
    router_default_scope = os.getenv("UPDATES_ASSISTANT_ROUTER_DEFAULT_SCOPE", "channel").strip().lower()
    debug = os.getenv("UPDATES_ASSISTANT_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    log_channel_id_str = os.getenv("UPDATES_ASSISTANT_LOG_CHANNEL_ID", "")
    log_channel_id = int(log_channel_id_str) if log_channel_id_str.strip() else None

    return UpdatesAssistantConfig(
        enabled=enabled,
        allowed_channel_ids=allowed_channel_ids,
        rate_limit_seconds=rate_limit_seconds,
        default_period=default_period,
        llm_provider=llm_provider,
        llm_model=llm_model,
        reaction_emoji=reaction_emoji,
        chat_only=chat_only,
        context_max_messages=context_max_messages,
        context_include_bots=context_include_bots,
        reply_as_mention=reply_as_mention,
        router_enabled=router_enabled,
        router_llm_provider=router_llm_provider,
        router_llm_model=router_llm_model,
        router_llm_temperature=router_llm_temperature,
        router_llm_max_output_tokens=router_llm_max_output_tokens,
        router_default_scope=router_default_scope,
        debug=debug,
        log_channel_id=log_channel_id,
    )
