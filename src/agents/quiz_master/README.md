# Quiz Master Agent

Discord上で「知識問題 + クリエイティブ（AI採点）問題」を運営がコマンドで進行できるエージェントです。

## 参加者のUX
- Botが各問題をフォーラムに投稿
- 参加者は投稿内に回答メッセージを送るだけ（自由入力）
- 締切後、Botが採点・ランキング表示（Claude採点対応）

## 運営コマンド
- `!quiz start [config]` : クイズ開始（このチャンネルで実行）
- `!quiz next` : 次の問題を出す
- `!quiz close` : 現在の問題を締めて採点
- `!quiz leaderboard` : ランキング表示
- `!quiz end` : 終了（seed公開 & 優勝発表）
- `!quiz reset` : 状態リセット

## 設定（環境変数）
- `QUIZ_ADMIN_USER_IDS` : 運営コマンドを実行できる user_id のCSV（例: `123,456`）
- `QUIZ_ADMIN_ROLE_IDS` : （任意）運営role_idのCSV
- `QUIZ_ADMIN_CHANNEL_ID` : 運営コマンドを受け付けるチャンネルID
- `QUIZ_FORUM_CHANNEL_ID` : クイズ投稿先のフォーラムID
- `QUIZ_LLM_PROVIDER` : `gemini` / `claude`（デフォルト: gemini）
- `QUIZ_DEFAULT_CONFIG` : デフォルトのクイズJSON（例: `src/agents/quiz_master/quizzes/bonenkai_2025.json`）
- `QUIZ_GEMINI_MODEL` : GeminiモデルID（例: `gemini-3-flash-preview`）
- `QUIZ_CLAUDE_MODEL` : ClaudeモデルID（例: `claude-sonnet-4-5`）
- `GOOGLE_API_KEY` or `GEMINI_API_KEY` : Gemini API key
- `ANTHROPIC_API_KEY` : Claude API key

## 注意
- 参加者の回答内容は採点のためにLLMへ送られます（運営側で事前アナウンス推奨）。
- クリエイティブ採点はLLM評価なのでブレる可能性があります。必要なら「人間が最終確定」も可能です。
