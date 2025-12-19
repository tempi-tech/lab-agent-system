# Repository Guidelines

## Project Structure & Module Organization
- `main.py` is the entry point; it builds the Discord client and registers agents.
- `src/core/` holds platform code (Discord client wrapper, config loader, base agent class).
- `src/agents/` contains plugin-style agents; each agent lives in its own folder with `__init__.py`, `config.py`, and logic modules (example: `src/agents/daily_reporter/`).
- `scripts/` hosts one-off utilities; `docs/` and `data/` store reference material and assets.

## Build, Test, and Development Commands
- `pip install .` installs runtime dependencies from `pyproject.toml`.
- `python main.py` runs the bot locally.
- `python main.py --once` runs a single cycle (used by GitHub Actions).
- `docker compose up` starts the bot in Docker.
- `black src scripts && isort src scripts` formats Python code.
- `mypy src` runs static type checks.
- `pytest` runs the test suite (add tests as you introduce them).

## Coding Style & Naming Conventions
- Formatting is enforced with Black + isort; keep imports sorted and line length default.
- Use type hints for public interfaces.
- Naming: modules `snake_case`, classes `CapWords`, constants/env vars `UPPER_SNAKE`.
- Agent-specific settings belong in `src/agents/<agent_name>/config.py`; secrets are loaded from `.env` via `src/core/config.py`.

## Testing Guidelines
- Test framework: `pytest`.
- Prefer `tests/` with files named `test_*.py`; mirror `src/` structure when possible.
- For agent behaviors, mock `discord.py` objects and keep API calls behind small helpers.

## Commit & Pull Request Guidelines
- Follow conventional commits seen in history: `feat:`, `fix:`, `refactor:` (short, imperative summaries; Japanese or English is fine).
- Keep commits focused and avoid mixing refactors with feature changes.
- PRs should include: what/why, testing performed (`pytest`, `mypy`, or manual), and screenshots or sample Discord output for user-facing changes.

## Agent Development Notes
- All agents must inherit from `BaseAgent` (`src/core/agent_base.py`).
- Export a `get_agent()` in each agent’s `__init__.py` and register the agent in `main.py`.
- Ensure agents can run in both long-running and `--once` modes.

## Security & Configuration Tips
- Never commit `.env`; start from `.env.example` for local setup.
- For scheduled runs, use GitHub Actions secrets (`DISCORD_TOKEN`, `GOOGLE_API_KEY`, etc.).
