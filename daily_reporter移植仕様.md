# daily_reporter移植仕様

作成日: 2026-03-11
対象移植先: `/Users/kai/.openclaw` 系 runtime
移植方針: **コード移植ではなく仕様移植**

## 1. この機能の役割

Discord コミュニティの過去24時間の会話を集め、
1本の「今日のラボ日誌」を生成して投稿する。

追加で、条件に応じて:

- Tips thread を投稿する
- ラジオ台本 JSON を保存する
- 音声ファイルを生成・投稿する
- 投稿結果を SQLite に保存する

## 2. 現行コード上の入口

- 実行経路:
  - `python main.py --once`
- 実装:
  - [src/agents/daily_reporter/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/logic.py)
  - [src/agents/daily_reporter/config.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/config.py)
  - [src/agents/daily_reporter/storage.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/storage.py)
  - [src/agents/daily_reporter/radio.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/daily_reporter/radio.py)
- 定期実行:
  - [daily_report.yml](/Users/kai/Develop/autogen/lab-agent-system/.github/workflows/daily_report.yml)

## 3. 入力

### 3-1. Discord 入力

- `SOURCE_CHANNEL_IDS`
- `SOURCE_CATEGORY_IDS`
- `SOURCE_CHANNEL_EXCLUDE_IDS`

`resolve_source_channels()` は:

- 明示チャンネルを足す
- category 配下の Text/Forum channel を展開する
- exclude を引く

### 3-2. 期間

- UTC now から24時間前までを対象
- 実質「直近24時間の履歴」

### 3-3. 環境変数

最低限:

- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID`
- `GOOGLE_API_KEY`
- `SOURCE_CHANNEL_IDS` または `SOURCE_CATEGORY_IDS`

音声機能を使うなら:

- `DAILY_REPORT_AUDIO_ENABLED`
- `DAILY_REPORT_TTS_MODEL`
- `DAILY_REPORT_RADIO_*`

## 4. 処理フロー

### Step 1. 対象チャンネル解決

- TextChannel は `channel.history(after=threshold)`
- ForumChannel は active thread をたどって `thread.history(after=threshold)`
- 現行 code は archived thread を読まない

### Step 2. メッセージ整形

各メッセージについて:

- author display 名を決定
- admin なら `[Admin]` を付ける
- 本文中の外部URLは削除または placeholder 化
- `jump_url` は保持
- `Location` は `parent > thread/channel` 形式もありうる

### Step 3. 新メンバー検知

- `guild.members` を走査
- `joined_at > threshold`
- bot は除外

### Step 4. LLM パイプライン

現行は Google ADK の `SequentialAgent + ParallelAgent` で以下を実行:

- `TopicSummarizer`
- `HighlightScout`
- `TipsScout`
- `EditorInChief`
- `RadioScriptWriter`

OpenClaw 側では同じクラス構成を再現する必要はない。
ただし **出力責務** は維持すること。

### Step 5. 投稿

- 対象チャンネルの webhook `ADK Summary Webhook` を再利用または作成
- avatar があれば更新
- 生成レポートを webhook で投稿
- Tips があれば thread を追加投稿
- 音声が生成され、サイズ条件を満たせば webhook で投稿

### Step 6. 永続化

- main report を `DailyDigestStore` に upsert
- 保存内容:
  - `message_id`
  - `channel_id`
  - `created_at`
  - `content`
  - `extracted_channels`

### Step 7. ラジオ台本 / 音声

- `RadioScriptWriter` の JSON を保存
- `GOOGLE_API_KEY` で Gemini TTS を叩く
- single-pass 失敗時は multi-pass fallback
- mp3 変換後、サイズが `RADIO_MAX_UPLOAD_BYTES` 以下なら投稿

## 5. 出力

### 5-1. メイン投稿

フォーマットは compact な日報。
重要な制約:

- 先頭は `📅 **今日のラボ日誌**`
- Discord URL のみ残す
- 外部URLは投稿しない
- トピック箇条書きは LLM 生成だが Discord URL を含むこと
- 新メンバーがいれば最下部に追加

### 5-2. Tips thread

- `tips_text != "なし"` のときのみ
- メイン投稿の thread / follow-up として送る

### 5-3. 音声投稿

- 音声機能が有効
- script parse 成功
- TTS 成功
- mp3 サイズ条件クリア

## 6. OpenClaw 側で保持すべき仕様

### 必須

- 24時間窓
- channel/category/exclude 解決
- Text + Forum active threads 読み取り
- Discord URL のみを残すサニタイズ
- 新メンバー検知
- 1本の compact report 投稿
- run record の保存
- cron での定期実行

### あるとよい

- Tips follow-up
- ラジオ台本保存
- 音声生成と投稿

### 後回し可

- avatar 更新の細かい挙動
- ADK そのものの再現

## 7. 失敗しやすい点

- source channels 未設定で空振りする
- Forum の archived threads を読んでいないため、OpenClaw 側で挙動差が出る
- LLM が外部URLや変形した URL を混ぜる
- webhook 作成権限不足
- 音声生成が時間超過・サイズ超過
- GitHub Actions 前提だった scheduler を OpenClaw cron に置き換え忘れる

## 8. OpenClaw 実装方針

おすすめの分解:

1. `collect_daily_messages`
2. `detect_new_members`
3. `build_digest_inputs`
4. `generate_daily_report`
5. `publish_daily_report`
6. `persist_digest_record`
7. `generate_radio_assets`（任意）

## 9. Shadow Run 条件

最初の切替はしない。
先に OpenClaw 側で:

- 別チャンネルへ投稿
- 旧系と同日比較
- 1週間ほど比較

比較指標:

- 実行時刻
- 投稿失敗率
- トピック数
- URL 品質
- 新メンバー検知差分

## 10. 切替条件

- 連続数日 shadow run で安定
- Discord 投稿失敗なし
- 重大な内容劣化なし
- ログ保存と再実行手順が確立
- rollback 手順が書面化済み

