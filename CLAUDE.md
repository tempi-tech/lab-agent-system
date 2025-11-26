# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies (Python >=3.10)
pip install .

# Run bot locally
python main.py

# Run once and exit (for GitHub Actions / testing)
python main.py --once

# Docker
docker compose up

# Format code
black src scripts && isort src scripts

# Type check
mypy src

# Run tests
pytest
```

## Architecture Overview

This is a Discord bot platform that runs multiple AI agents on a single bot process. Uses Google's ADK (Agent Development Kit) with Gemini for AI capabilities.

### Core Components

- **`main.py`**: Entry point. Initializes Discord client, registers agents, handles `--once` flag for single-run mode (GitHub Actions)
- **`src/core/bot.py`**: `CommunityBot` class extending `discord.Client`. Manages agent registration and dispatches `on_ready`/`on_message` events to all registered agents
- **`src/core/agent_base.py`**: `BaseAgent` ABC that all agents must inherit. Requires `name` property; optional `on_ready(client)` and `on_message(message)` hooks
- **`src/core/config.py`**: Loads environment variables via dotenv

### Agent System

Agents live in `src/agents/<agent_name>/`:
- Each agent folder contains `__init__.py` exposing `get_agent()` function
- Agent class inherits `BaseAgent` and implements the required interface
- Agent-specific config in `<agent_name>/config.py`

Currently implemented: **DailyReporterAgent** (`src/agents/daily_reporter/`)
- Fetches 24h of messages from configured channels
- Uses Google ADK's `ParallelAgent` + `SequentialAgent` to run multiple LLM analysis agents (TopicSummarizer, HighlightScout, LinkCurator)
- Final output via EditorInChief agent with "ラボちゃん" persona
- Posts via Discord webhook with custom avatar

### Environment Variables

Required:
- `DISCORD_TOKEN`: Bot token
- `DISCORD_CHANNEL_ID`: Target channel for posting reports
- `SOURCE_CHANNEL_IDS`: Comma-separated channel IDs to monitor
- `GOOGLE_API_KEY`: Gemini API key

Optional:
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`: For Vertex AI

## Adding a New Agent

1. Create `src/agents/<name>/` directory
2. Create class inheriting `BaseAgent` with `name` property
3. Implement `on_ready(client)` and/or `on_message(message)` as needed
4. Export via `get_agent()` in `__init__.py`
5. Register in `main.py`: `client.register_agent(YourAgent())`

## Code Style

- Format: `black` + `isort`
- Type hints for public APIs
- Module names: `snake_case`, classes: `CapWords`, env vars: `UPPER_SNAKE`
- Commits: conventional format (`feat:`, `fix:`, `refactor:`)
