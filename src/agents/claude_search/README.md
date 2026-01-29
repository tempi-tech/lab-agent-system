# Claude Search Agent

## Overview
Claude Search provides Discord search and Q&A commands:
- `!csearch` lists matching messages with jump links.
- `!cask` searches and summarizes answers using an LLM with sources.

Search uses the Discord guild message search API and filters results to channels
the requesting member can view.

## Usage
- `!csearch <keyword>`
- `!cask <question>`

## Configuration (env)
Core:
- `CLAUDE_SEARCH_ENABLED` (default: true)
- `CLAUDE_SEARCH_MAX_RESULTS`
- `CLAUDE_SEARCH_DISCORD_SEARCH_LIMIT`
- `CLAUDE_SEARCH_RATE_LIMIT_SECONDS`
- `CLAUDE_SEARCH_LLM_PROVIDER` / `CLAUDE_SEARCH_LLM_MODEL`
- `CLAUDE_SEARCH_LLM_TEMPERATURE`
- `CLAUDE_SEARCH_LLM_MAX_OUTPUT_TOKENS`

Logging:
- `CLAUDE_SEARCH_LOG_CHANNEL_ID`
- `CLAUDE_SEARCH_DEBUG`

Required:
- `DISCORD_TOKEN` (for Discord search API)
- `OPENROUTER_API_KEY` (if provider is OpenRouter)

## Behavior Notes
- Returns ERR_DISCORD_SEARCH on HTTP failures or timeouts (not “no results”).
- Rate limiting is per-user when enabled.
- Output is chunked to stay under Discord message limits.

## Troubleshooting
- “Discord 検索に失敗しました”: check `DISCORD_TOKEN`, bot permissions, and rate limits.
- “該当するメッセージが見つかりませんでした”: valid search with no matches.
- Large outputs not showing: ensure chunking is enabled (it is by default).

## Tests & Tools
- `tests/core/test_discord_search.py`
- `tests/core/test_discord_access.py`
- `tests/core/test_llm_client.py`
- `scripts/discord_search_cli.py`
- `scripts/claude_search_smoke.py` (requires `.env` and `PYTHONPATH=.`)
