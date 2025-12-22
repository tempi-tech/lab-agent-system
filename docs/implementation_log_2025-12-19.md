# 実装ログ（2025-12-19）

## 概要
DailyReporter が不要なタイミングで自動投稿する問題への対処として、
**「呼び出された時だけ動く」**構成へ移行し、
GitHub Actions の `--once` 実行は **DailyReporter のみ**を起動するよう整理した。

この変更により、通常運用では他エージェントのテストが安全になり、
日報は明示的な呼び出しまたは `--once` 実行に限定される。

## 背景
- DailyReporter が `on_ready` で自動実行されるため、
  他エージェント（QuizMaster / InviteRoleAssigner）の検証時にも日報が投稿されていた。
- GitHub Actions は `python main.py --once` を使うが、
  そのとき InviteRoleAssigner が必須 env を要求して落ちる問題が発生した。

## 変更内容（要点）
### 1) エージェントの「関数化」と手動呼び出し
- **ActionRegistry** を追加（`src/core/action_registry.py`）
  - エージェントが公開する関数を登録・列挙できる仕組み。
- **OperatorAgent** を追加（`src/agents/operator/`）
  - `!agent <namespace> <action> [args...]` で関数を呼び出す。
  - 例: `!agent daily_reporter run`
- **DailyReporter** に `get_actions()` を追加し、
  `run` を公開（`src/agents/daily_reporter/logic.py`）。
  - `run` は `DISCORD_CHANNEL_ID` か `here` 指定で投稿先を選択。
  - `on_ready` 自動実行は廃止。

### 2) `--once` 実行時の構成整理
- `main.py --once` のときは **DailyReporter のみ登録**するように変更。
  - InviteRoleAssigner の必須 env 不足によるエラーを回避。
- `--once` 実行時は DailyReporter を **1回だけ実行して終了**。

### 3) スモークテストの追加
- `scripts/smoke_test.py` を追加。
- `docs/smoke_test.md` に手順と期待結果を記載。
  - #test（ID: `CHANNEL_ID`）に固定して検証可能。

## 挙動の変化
- **通常起動（`python main.py`）**
  - DailyReporter は自動投稿しない。
  - Operator / QuizMaster / InviteRoleAssigner が通常通り起動。
  - 日報は `!agent daily_reporter run` で実行。

- **`--once` 起動（GitHub Actions 用）**
  - DailyReporter のみ起動。
  - `DISCORD_CHANNEL_ID` に対して 1回だけ日報を投稿して終了。

## テスト結果
- ローカルで `python main.py --once` を #test に向けて実行し、
  ログイン・分析・投稿・終了まで成功を確認。
- GitHub Actions で `--once` 実行時に
  InviteRoleAssigner が落ちる問題を修正済み。
  （再実行で確認予定）

## 追加されたファイル
- `src/core/action_registry.py`
- `src/agents/operator/__init__.py`
- `src/agents/operator/logic.py`
- `scripts/smoke_test.py`
- `docs/agent_activation_issue.md`
- `docs/smoke_test.md`

## 主要変更ファイル
- `main.py`（--once の登録構成を整理）
- `src/core/bot.py`（ActionRegistry 連携）
- `src/agents/daily_reporter/logic.py`（run アクション追加）
- `AGENTS.md`（ガイド追加）

## 関連ドキュメント
- 課題整理: `docs/agent_activation_issue.md`
- スモークテスト: `docs/smoke_test.md`
- InviteRoleAssigner 実装ログ: `docs/invite_role_assigner_implementation_log.md`

## 次の確認事項（TODO）
- GitHub Actions の手動実行で #test へ日報が出ることを確認。
- 必要なら OperatorAgent に最小の権限チェックを追加。

## トラブルシュート記録
- `docs/forum_quiz_troubleshooting.md`
- `docs/quiz_forum_implementation_log_2025-12-19.md`
- `docs/quiz_forum_inspect.md`
- `docs/quiz_forum_overview.md`
