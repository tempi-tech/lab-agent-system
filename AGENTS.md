## Continuity Ledger (compaction-safe)
Maintain a single Continuity Ledger for this workspace in `CONTINUITY.md`. The ledger is the canonical session briefing designed to survive context compaction; do not rely on earlier chat text unless it’s reflected in the ledger.

### How it works
- At the start of every assistant turn: read `CONTINUITY.md`, update it to reflect the latest goal/constraints/decisions/state, then proceed with the work.
- Update `CONTINUITY.md` again whenever any of these change: goal, constraints/assumptions, key decisions, progress state (Done/Now/Next), or important tool outcomes.
- Keep it short and stable: facts only, no transcripts. Prefer bullets. Mark uncertainty as `UNCONFIRMED` (never guess).
- If you notice missing recall or a compaction/summary event: refresh/rebuild the ledger from visible context, mark gaps `UNCONFIRMED`, ask up to 1–3 targeted questions, then continue.

### `functions.update_plan` vs the Ledger
- `functions.update_plan` is for short-term execution scaffolding while you work (a small 3–7 step plan with pending/in_progress/completed).
- `CONTINUITY.md` is for long-running continuity across compaction (the “what/why/current state”), not a step-by-step task list.
- Keep them consistent: when the plan or state changes, update the ledger at the intent/progress level (not every micro-step).

### In replies
- Begin with a brief “Ledger Snapshot” (Goal + Now/Next + Open Questions). Print the full ledger only when it materially changes or when the user asks.

### `CONTINUITY.md` format (keep headings)
- Goal (incl. success criteria):
- Constraints/Assumptions:
- Key decisions:
- State:
- Done:
- Now:
- Next:
- Open questions (UNCONFIRMED if needed):
- Working set (files/ids/commands):

# Repository Guidelines

## Project Structure & Module Organization
- `main.py` is the entry point; it builds the Discord client and registers agents.
- `src/core/` holds platform code (Discord client wrapper, config loader, base agent class).
- `src/agents/` contains plugin-style agents; each agent typically has `__init__.py`, `config.py`, and logic modules (some agents use `agent.py` or omit `config.py`).
- `scripts/` hosts one-off utilities; `docs/` and `data/` are local/internal (gitignored in OSS).

## Build, Test, and Development Commands
- `pip install .` installs runtime dependencies from `pyproject.toml`.
- `python main.py` runs the bot locally.
- `python main.py --once` runs a single cycle (used by GitHub Actions).
- `docker compose up` starts the bot in Docker.
- `black src scripts && isort src scripts` formats Python code.
- `mypy src` runs static type checks.
- `pytest` runs the test suite (add tests as you introduce them).
- Dev tools may not be installed by `pip install .`; install them separately (e.g., `uv sync --dev`).

## Coding Style & Naming Conventions
- Formatting is enforced with Black + isort; keep imports sorted and line length default.
- Use type hints for public interfaces.
- Naming: modules `snake_case`, classes `CapWords`, constants/env vars `UPPER_SNAKE`.
- Agent-specific settings belong in `src/agents/<agent_name>/config.py`; secrets are loaded from environment via `src/core/config.py`.

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
- Prefer exporting a `get_agent()` in each agent’s `__init__.py` and register via `main.py` (some legacy agents are instantiated directly).
- Ensure agents can run in both long-running and `--once` modes.

## Security & Configuration Tips
- This repository is public OSS; assume anything committed is world-readable.
- Store secrets outside the repo (GitHub Actions secrets, secret managers, or local environment variables).
- Local-only files are intentionally ignored: `CONTINUITY.md`, `.claude/`, logs, `docs/`, and `data/`.

## Project-specific Gotchas
- OpenRouter: some models return reasoning without content; disable reasoning when needed (`OPENROUTER_REASONING_EFFORT=none` or `OPENROUTER_EXCLUDE_REASONING=true`).
- Discord: 2000-char limit; split responses around ~1900 chars and chunk single long lines.
- Discord search: treat 429/4xx/5xx and timeouts as ERR_DISCORD_SEARCH, not “no results”.
- CLI scripts: run with `PYTHONPATH=.` and source `.env` when API keys are required.
- Some scripts need extra env vars not in `.env.example`; check each script’s header/usage.
