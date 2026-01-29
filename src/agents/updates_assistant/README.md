# Updates Assistant

## Overview
Updates Assistant provides chat, update summaries, and log Q&A inside Discord.
Mentions (or replies) are routed by an LLM to one of: chat, log summary, or log Q&A.
You can also force a mode with commands when chat-only mode is disabled.

## Usage
- Mention or reply to the bot for chat.
- Commands (when `UPDATES_ASSISTANT_CHAT_ONLY=false`):
  - `!updates [1h|6h|24h|7d]`
  - `!ask <question>`

## Configuration (env)
Core:
- `UPDATES_ASSISTANT_ENABLED` (default: true)
- `UPDATES_ASSISTANT_ALLOWED_CHANNEL_IDS` (comma-separated channel IDs; empty = all)
- `UPDATES_ASSISTANT_RATE_LIMIT_SECONDS` (0 disables rate limiting)
- `UPDATES_ASSISTANT_DEFAULT_PERIOD` (e.g., `24h`)
- `UPDATES_ASSISTANT_LLM_PROVIDER` / `UPDATES_ASSISTANT_LLM_MODEL`
- `UPDATES_ASSISTANT_CHAT_ONLY` (true = always chat, router ignored)
- `UPDATES_ASSISTANT_REACTION_EMOJI` (e.g., `:agi2:` or a unicode emoji)

Context:
- `UPDATES_ASSISTANT_CONTEXT_MAX_MESSAGES`
- `UPDATES_ASSISTANT_CONTEXT_INCLUDE_BOTS`
- `UPDATES_ASSISTANT_REPLY_AS_MENTION`

Router:
- `UPDATES_ASSISTANT_ROUTER_ENABLED`
- `UPDATES_ASSISTANT_ROUTER_LLM_PROVIDER` / `UPDATES_ASSISTANT_ROUTER_LLM_MODEL`
- `UPDATES_ASSISTANT_ROUTER_LLM_TEMPERATURE`
- `UPDATES_ASSISTANT_ROUTER_LLM_MAX_OUTPUT_TOKENS`
- `UPDATES_ASSISTANT_ROUTER_DEFAULT_SCOPE` (`channel` or `guild`)

Logging:
- `UPDATES_ASSISTANT_LOG_CHANNEL_ID`
- `UPDATES_ASSISTANT_DEBUG`

OpenRouter (if used):
- `OPENROUTER_API_KEY`
- `OPENROUTER_REASONING_EFFORT=none` or `OPENROUTER_EXCLUDE_REASONING=true` to avoid empty content on some models.

## Behavior Notes
- Rate limiting is per-user when `UPDATES_ASSISTANT_RATE_LIMIT_SECONDS > 0`.
- A reaction emoji is added to the incoming message instead of a pre-answer status message.
- Reply-to-bot is treated as a mention when `UPDATES_ASSISTANT_REPLY_AS_MENTION=true`.
- Context includes recent messages from the same channel; bot messages are included only when enabled.

## Troubleshooting
- Empty responses with certain OpenRouter models: set `OPENROUTER_REASONING_EFFORT=none`.
- No response on replies: ensure `UPDATES_ASSISTANT_REPLY_AS_MENTION=true`.
- Log fetch failures: confirm the bot has `Read Message History` and access to the channel(s).
- Duplicate replies: ensure only one bot instance is running.

## Tests & Tools
- `tests/agents/updates_assistant/test_router.py`
- `scripts/updates_assistant_router_smoke.py` (requires `.env` and `PYTHONPATH=.`)
