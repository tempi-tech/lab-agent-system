# Quiz フォーラム化 + Claude採点 実装ログ（2025-12-19）

## 目的
- QuizMaster を **スレッド運用からフォーラム投稿運用**に移行。
- クリエイティブ問題の採点を **Claude** で行い、
  **5観点スコア + コメント**を返す。

## 変更概要
### フォーラム運用
- `QUIZ_FORUM_CHANNEL_ID` を追加し、問題はフォーラム投稿として作成。
- 参加者は **投稿内に回答**する形式へ変更。
- 運営コマンドは `QUIZ_ADMIN_CHANNEL_ID` で固定。

### Claude採点
- `QUIZ_LLM_PROVIDER=claude` で Claude採点に切り替え。
- 出力は JSON で **total + criteria + comment**。
- 結果表示に **観点スコア（O/C/R/I/H）** を付与。

## 実装詳細
### 主要ファイル
- `src/agents/quiz_master/agent.py`
  - フォーラム投稿作成に移行
  - 運営チャンネル固定
  - 結果の観点スコア表示
  - フォーラム投稿内にも結果を出力

- `src/agents/quiz_master/scoring.py`
  - Claude API (Messages) に対応
  - 5観点スコア + コメント形式の JSON をパース

- `src/agents/quiz_master/README.md`
  - 設定項目を追加

- `.env.example`
  - Claude用設定項目を追加

## 追加/更新した環境変数
- `QUIZ_ADMIN_CHANNEL_ID` : 運営コマンド受付チャンネル
- `QUIZ_FORUM_CHANNEL_ID` : フォーラム投稿先
- `QUIZ_LLM_PROVIDER` : `gemini` / `claude`
- `QUIZ_CLAUDE_MODEL` : Claudeモデル名
- `ANTHROPIC_API_KEY` : Claude API Key
- `QUIZ_DEBUG` : デバッグログ出力

## テストメモ
- Claude採点は `QUIZ_LLM_PROVIDER=claude` で動作。
- フォーラム投稿が複製される場合は **Botの二重起動**が原因。
- `.env` に改行欠落があると admin ID が読めずコマンド無反応になる。

## 関連ドキュメント
- `docs/forum_quiz_troubleshooting.md`
- `docs/implementation_log_2025-12-19.md`
