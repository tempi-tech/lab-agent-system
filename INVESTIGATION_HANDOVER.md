# Investigation Handover: GitHub Actions "No Messages" Issue

## 🚨 The Problem
The **Daily Reporter Bot** works perfectly when run locally (fetches messages and generates a report).
However, when run via **GitHub Actions** (Scheduled or Manual), it consistently reports:
> "今日は静かな一日でしたね。（メッセージも新メンバーもなし）"
> (Translation: "It was a quiet day today. No messages or new members.")

This implies that the bot is running successfully but failing to fetch any messages from the source channels, or it thinks there are no source channels.

## 📂 Project Context
*   **Project Name**: Lab Agent System
*   **Type**: Discord Bot (Python `discord.py`)
*   **Architecture**:
    *   **Platform**: `src/core/` (Bot connection)
    *   **Agent**: `src/agents/daily_reporter/` (Logic for fetching & summarizing)
    *   **Runner**: `main.py` (Entry point, supports `--once` flag for CI/CD)

## 🛠 Recent Changes (Important)
We recently refactored the **Source Channel IDs** from hardcoded values to an environment variable.
*   **Old**: Hardcoded list in `config.py`.
*   **New**: Loaded from `SOURCE_CHANNEL_IDS` env var (comma-separated).
*   **Fix**: We patched `logic.py` to handle empty `SOURCE_CHANNEL_IDS` gracefully (printing a warning instead of crashing).

## 🔍 Key Files to Investigate

1.  **`src/agents/daily_reporter/logic.py`**
    *   Contains `fetch_daily_messages` and `generate_summary`.
    *   Look at how `config.SOURCE_CHANNELS` is iterated.
    *   Look for the "Warning: No source channels configured" print statement.

2.  **`src/agents/daily_reporter/config.py`**
    *   Parses `os.environ.get("SOURCE_CHANNEL_IDS")`.
    *   Splits by comma and converts to `int`.

3.  **`.github/workflows/daily_report.yml`**
    *   Defines the workflow.
    *   Passes `SOURCE_CHANNEL_IDS: ${{ secrets.SOURCE_CHANNEL_IDS }}`.

## 🧪 Hypotheses / What to Check

1.  **Secret Configuration**: Is `SOURCE_CHANNEL_IDS` correctly set in GitHub Secrets? If it's empty or malformed (e.g., contains quotes `'123,456'`), the parsing logic might fail or result in an empty list.
    *   *Check*: Does the GitHub Actions log show "Warning: No source channels configured"?
2.  **Timezone / Timestamp**: The bot uses `datetime.now(timezone.utc) - timedelta(hours=24)`.
    *   GitHub Runners are UTC. Local is JST.
    *   Is it possible the calculation is correct but the "window" it checks is actually empty in UTC time? (Unlikely if local works, but worth verifying).
3.  **Permissions / Network**: Does the GitHub Runner IP have issues connecting to Discord Gateway? (Unlikely as it logs in successfully).

## 📝 Action Plan for New Agent
1.  **Ask the user for the latest GitHub Actions Log**.
    *   Specifically look for the line: `Total messages fetched: X` or `Warning: No source channels configured`.
2.  **Verify `config.py` parsing logic**.
    *   Does it handle spaces? `123, 456` vs `123,456`. (It currently strips, but double check).
3.  **Debug the Secret**.
    *   Ask the user to temporarily add a step to print the *length* or *masked value* of the secret in the workflow (carefully) to ensure it's being passed.
