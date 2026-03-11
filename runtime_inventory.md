# Runtime Inventory

作成日: 2026-03-11
ブランチ: `migration-source`

## 1. 目的

Mac mini 側の OpenClaw エージェントに `lab-agent-system` を読ませる際、
「今の runtime で本当に動いているもの」と「参照だけに留めるもの」を分けるための棚卸し。

この文書は **current `main` 相当の runtime 実態** を要約する。
README や `.env.example` よりも、`main.py` と実コード参照を優先する。

## 2. Active Runtime

### エントリポイント

- [main.py](/Users/kai/Develop/autogen/lab-agent-system/main.py)
- Discord bot runtime は [src/core/bot.py](/Users/kai/Develop/autogen/lab-agent-system/src/core/bot.py)

### 実際に登録される agent

- `daily_reporter`
  - 通常起動では未登録
  - `python main.py --once` の既定ターゲット
- `membership_checker`
  - `python main.py --once membership` で起動
  - 通常起動でも登録される
- `quiz_master`
- `invite_role_assigner`
- `operator`
- `lab_onboarder`
- `updates_assistant`
- `claude_search`

## 3. Active Integrations

### Discord

- Bot runtime / event handling / webhooks / invite handling / role assignment
- Source:
  - [main.py](/Users/kai/Develop/autogen/lab-agent-system/main.py)
  - [src/core/bot.py](/Users/kai/Develop/autogen/lab-agent-system/src/core/bot.py)
  - [src/agents/invite_role_assigner/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/invite_role_assigner/logic.py)

### Google Gemini / google-genai

- `daily_reporter`
- `daily_reporter` radio TTS
- `lab_onboarder`
- `quiz_master`
- Source:
  - [src/core/llm.py](/Users/kai/Develop/autogen/lab-agent-system/src/core/llm.py)
  - [src/agents/daily_reporter/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/logic.py)
  - [src/agents/daily_reporter/radio.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/radio.py)
  - [src/agents/lab_onboarder/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/lab_onboarder/logic.py)
  - [src/agents/quiz_master/agent.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/quiz_master/agent.py)

### Google ADK

- `daily_reporter` の要約パイプラインで実使用
- Source:
  - [pyproject.toml](/Users/kai/Develop/autogen/lab-agent-system/pyproject.toml)
  - [src/agents/daily_reporter/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/logic.py)

### OpenRouter

- `updates_assistant`
- `claude_search`
- Source:
  - [src/agents/updates_assistant/config.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/updates_assistant/config.py)
  - [src/agents/claude_search/config.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/claude_search/config.py)

### Local persistence

- SQLite
  - `data/daily_reporter/digests.sqlite`
  - `data/lab_onboarder/profiles.sqlite`
- CSV/JSON
  - `data/invite_role_assigner/member_log.csv`
  - `data/invite_role_assigner/config.json`
  - `data/invite_role_assigner/sync_allowlist.json`

## 4. Scheduled / Run-Once Paths

### Daily report

- GitHub Actions:
  - [daily_report.yml](/Users/kai/Develop/autogen/lab-agent-system/.github/workflows/daily_report.yml)
- 実行コマンド:
  - `python main.py --once`

### Membership check

- GitHub Actions:
  - [membership_check.yml](/Users/kai/Develop/autogen/lab-agent-system/.github/workflows/membership_check.yml)
- 実行コマンド:
  - `python main.py --once membership`

## 5. Optional / Manual Systems

- Docker runtime
  - [docker-compose.yml](/Users/kai/Develop/autogen/lab-agent-system/docker-compose.yml)
- 手動スクリプト
  - `scripts/discord_search_cli.py`
  - `scripts/llm_smoke.py`
  - `scripts/openrouter_probe.py`
  - `scripts/daily_reporter_dry_run.py`
  - `scripts/export_discord_messages.py`
  - ハッカソン用スクリプト群

これらは useful ではあるが、**OpenClaw 初回移行の対象外** とする。

## 6. Archive / Ignore Candidates

- `question_sla`
- `community_analytics`
- `closed_loop` 系の成果物
- README 上の古い説明
- env/config にしか存在しない `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`

## 7. OpenClaw 移行で keep する能力

- `daily_reporter`
- role assignment
  - `invite_role_assigner`
  - `membership_checker` のうち role lifecycle に必要な部分

## 8. OpenClaw 側へ渡すときの注意

- public GitHub は参照元として使う
- ただし唯一の真実にはしない
- 併せて以下を渡す
  - [openclaw移行計画.md](/Users/kai/Develop/autogen/lab-agent-system/openclaw移行計画.md)
  - `daily_reporter移植仕様.md`
  - `role_assignment移植仕様.md`
- local dirty state に含まれる差分は必要に応じて `migration-source` に反映する

