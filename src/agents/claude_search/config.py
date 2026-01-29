import os
from dataclasses import dataclass


def _is_enabled(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no"}


@dataclass(frozen=True)
class ClaudeSearchConfig:
    enabled: bool
    max_results: int
    discord_search_limit: int
    rate_limit_seconds: int
    llm_provider: str
    llm_model: str
    llm_temperature: float
    llm_max_output_tokens: int
    debug: bool
    log_channel_id: int | None


def load_config() -> ClaudeSearchConfig:
    enabled = _is_enabled(os.getenv("CLAUDE_SEARCH_ENABLED", "true"))
    max_results = int(os.getenv("CLAUDE_SEARCH_MAX_RESULTS", "8"))
    discord_search_limit = int(os.getenv("CLAUDE_SEARCH_DISCORD_SEARCH_LIMIT", "20"))
    rate_limit_seconds = int(os.getenv("CLAUDE_SEARCH_RATE_LIMIT_SECONDS", "15"))
    llm_provider = os.getenv("CLAUDE_SEARCH_LLM_PROVIDER", "openrouter").strip()
    llm_model = os.getenv("CLAUDE_SEARCH_LLM_MODEL", "deepseek/deepseek-chat")
    llm_temperature = float(os.getenv("CLAUDE_SEARCH_LLM_TEMPERATURE", "0.2"))
    llm_max_output_tokens = int(os.getenv("CLAUDE_SEARCH_LLM_MAX_OUTPUT_TOKENS", "800"))
    debug = os.getenv("CLAUDE_SEARCH_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    log_channel_id_str = os.getenv("CLAUDE_SEARCH_LOG_CHANNEL_ID", "")
    log_channel_id = int(log_channel_id_str) if log_channel_id_str.strip() else None

    return ClaudeSearchConfig(
        enabled=enabled,
        max_results=max_results,
        discord_search_limit=discord_search_limit,
        rate_limit_seconds=rate_limit_seconds,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_temperature=llm_temperature,
        llm_max_output_tokens=llm_max_output_tokens,
        debug=debug,
        log_channel_id=log_channel_id,
    )
