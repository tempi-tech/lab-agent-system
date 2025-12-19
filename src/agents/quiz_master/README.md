# Quiz Master Agent

Discord上で「知識問題 + クリエイティブ（AI採点）問題」を運営がコマンドで進行できるエージェントです。

## 参加者のUX
- Botが各問題を投稿 → 自動でスレッド作成
- 参加者はスレッドに回答メッセージを送るだけ
- 締切後、Botが採点・ランキング表示

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
- `QUIZ_DEFAULT_CONFIG` : デフォルトのクイズJSON（例: `src/agents/quiz_master/quizzes/bonenkai_2025.json`）
- `QUIZ_GEMINI_MODEL` : GeminiモデルID（例: `gemini-3-flash-preview`）
- `GOOGLE_API_KEY` or `GEMINI_API_KEY` : Gemini API key

## 注意
- 参加者の回答内容は採点のためにLLMへ送られます（運営側で事前アナウンス推奨）。
- クリエイティブ採点はLLM評価なのでブレる可能性があります。必要なら「人間が最終確定」も可能です。
