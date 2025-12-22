# Smoke Test Plan（#test チャンネル運用）

## 目的
- **1コマンドで日報の実行確認**を行い、Discord投稿・集計・生成フローが動作することを確認する。
- テスト結果は **#test (ID: CHANNEL_ID)** に出力して確認する。

## 前提
- `.env` に以下が設定済みであること
  - `DISCORD_TOKEN`
  - `GOOGLE_API_KEY`
- テスト用チャンネル ID（#test）
- `SMOKE_TEST_CHANNEL_ID` を未設定なら **CHANNEL_ID** を使用
  - スモークテスト中は `DISCORD_CHANNEL_ID` と `SOURCE_CHANNEL_IDS` を #test に固定する

## テストコマンド（1回で完了）
```bash
python scripts/smoke_test.py
```

## 期待結果（#test で確認）
1. `🧪 Smoke test: start (daily_reporter)` が投稿される
2. DailyReporter が分析中メッセージと日報を投稿する
3. `✅ Smoke test: done` が投稿される

※ 上記以外の投稿が自動で出ないこと（DailyReporter が勝手に動かないこと）も合わせて確認する。

## 失敗時の切り分け
- `Missing env: DISCORD_TOKEN` / `GOOGLE_API_KEY`
  - `.env` に値が入っているか確認
- `Smoke test channel not found`
  - Bot が #test へアクセス可能か、ID が正しいか確認
- 日報が出ない
  - `SOURCE_CHANNEL_IDS` が未設定/空の場合は、内容が「静かな一日」になる可能性あり
- まずは `SOURCE_CHANNEL_IDS=CHANNEL_ID` を指定して再確認

## GitHub Actions テスト（1回だけ確認したい場合）
- Actions の `Daily Report` ワークフローを **workflow_dispatch** で手動実行する
- `channel_override` に `CHANNEL_ID`（#test）を入れると安全に確認できる
- 1回だけ本番チャンネルで確認したい場合は、`channel_override` にそのIDを入れる

## 実行記録
### 2025-12-19（ローカル / --once）
```
DISCORD_CHANNEL_ID=CHANNEL_ID \
SOURCE_CHANNEL_IDS=CHANNEL_ID \
python main.py --once
```
- CLI 出力: ログイン成功、#test のメッセージ取得、分析開始、完了ログを確認

## 関連ログ
- `docs/implementation_log_2025-12-19.md`
