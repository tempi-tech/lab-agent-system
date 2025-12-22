# システムスナップショット & ランブック（2025-12-19）

このドキュメントは**現時点の実装を1枚に統合**したものです。  
**コンテキスト0のLLM**でも理解できるように、**何があるか／どこにあるか／どう動かすか／どう運用するか**をまとめています。

---

## 0) このリポジトリの目的（1行）
Discordのラボ用マルチエージェントBotを動かし、**クイズ運営（QuizMaster）**・**招待ロール付与（InviteRoleAssigner）**・**日報（DailyReporter）**などを実行する。

---

## 1) エントリポイントと起動

### メイン
- `main.py` が構成の起点（Composition Root）。
  - **通常起動**: `quiz_master` / `invite_role_assigner` / `operator` を登録。
  - **run-once**: `python main.py --once` で **DailyReporterのみ**起動し、1回投稿して終了。

### Docker（本番推奨）
```
docker compose up -d
docker compose logs --tail=120
```
期待ログ:
- `Registered agent: quiz_master`
- `Registered agent: InviteRoleAssignerAgent`
- `Registered agent: operator`
- `Logged in as ...`

**注意**: Botの二重起動は「問題の重複投稿」の原因。

---

## 2) エージェント概要

### QuizMaster (`src/agents/quiz_master/agent.py`)
- **フォーラム投稿＝1問**のクイズ運用。
- `!quiz` コマンドは **運営チャンネルのみ**で有効。
- 回答は **該当フォーラム投稿内のみ**を採用。
- 結果は **運営チャンネル**と**フォーラム投稿内**の両方に出力。

### InviteRoleAssigner (`src/agents/invite_role_assigner`)
- 参加者の招待コードに応じてロールを付与。
- 必須envが欠けると**起動時に例外で停止**。

### DailyReporter (`src/agents/daily_reporter`)
- `DISCORD_CHANNEL_ID` に日報投稿。
- `--once` または `ENABLE_DAILY_REPORTER=1` の時のみ動作。

### Operator (`src/agents/operator`)
- `!agent <namespace> <action>` のコマンドルータ。
- 例: `!agent daily_reporter run`

---

## 3) 必須環境変数（現行運用）

### クイズ + 招待ロール（通常起動）
- `DISCORD_TOKEN`
- `QUIZ_ADMIN_USER_IDS`（CSV）
- `QUIZ_ADMIN_CHANNEL_ID`（運営チャンネル: **CHANNEL_ID**）
- `QUIZ_FORUM_CHANNEL_ID`（フォーラム: **FORUM_CHANNEL_ID**）
- `QUIZ_LLM_PROVIDER=claude`
- `ANTHROPIC_API_KEY`
- `ENABLE_DAILY_REPORTER=0`
- InviteRoleAssigner:
  - `INVROLE_GUILD_ID`
  - `INVROLE_GENERAL_ROLE_ID`
  - `INVROLE_REVIEW_ROLE_ID`
  - `INVROLE_ADMIN_ROLE_ID`
  - `INVROLE_LOG_CHANNEL_ID`
  - `INVROLE_INVITED_CODES`

### 任意
- `QUIZ_CLAUDE_MODEL`（例: `claude-sonnet-4-5`）
- `QUIZ_DEFAULT_CONFIG`（クイズJSON）
- `QUIZ_DEBUG=1`（運営チャンネルにデバッグ出力）
- `QUIZ_BOT_AVATAR_PATH`（Botアイコン画像のパス。例: `src/agents/quiz_master/Airi.jpg`）
- `QUIZ_WEBHOOK_NAME`（Webhook名。例: `QuizMaster Webhook`）

**よくある事故**: `.env`の改行欠落 → `QUIZ_ADMIN_USER_IDS` が読めず `!quiz` が無反応。

---

## 4) QuizMasterのファイル構成

```
src/agents/quiz_master/
├── agent.py          # Discordイベント、出題、採点、結果表示
├── scoring.py        # LLM採点（Claude / Gemini）
├── storage.py        # セッション保存（data/quiz_master/session.json）
├── config.py         # Quiz JSONの読み込み
├── utils.py          # コマンド解析など
└── quizzes/
    ├── bonenkai_2025.json          # 現行の3問セット
    └── creative_2025-12-19.json    # 単発クリエイティブ（1問）

.data/
└── quiz_master/session.json  # セッション状態（自動生成）
```

---

## 5) Quizの運用フロー（運営視点）

運営コマンド:
```
!quiz reset
!quiz start
!quiz next
!quiz close
!quiz leaderboard
!quiz end
```

参加者フロー:
1. BotがフォーラムにQ1投稿
2. 参加者が投稿内に回答
3. `!quiz close` で採点・結果
4. `!quiz next` で次の問題

回答の上書き:
- `allow_answer_edit=false`
- **最初の回答のみ有効**（二回目は無視）

---

## 6) 現在のクイズ構成（bonenkai_2025.json）

**3問構成（知識2問＋クリエイティブ1問）**

### Q1: EQ Bench 1位モデル（knowledge / exact）
- 正解: **Kimi-K2-Instruct**
- 大文字小文字の違いはOK（`kimi-k2-instruct`）
- **空白/ハイフン違いもOK**（`kimi k2 instruct`）
- **正誤判定は固定**、LLMは使わない（不正解コメントも固定文言）
- 出題文には EQ-Bench の簡単説明を含める

### Q2: サム・アルトマンの「悪夢」（knowledge / LLM）
**問題文**:
> サム・アルトマンが大人になってからも長年見続けた『ある悪夢』があります。それは、『〇〇』のために授業に出られなかったり、逆に授業に出ていて『〇〇』の重要な会議に出られなかったりするという内容です。〇〇に入る言葉は？

**正解要点**:
> 当時の自身の会社（スタートアップ）/ 起業

**採点**:
- Claudeが0〜20点（整数）で部分点採点
- 4観点: originality / clarity / relevance / insight

### Q3: クリエイティブ（LLM）
**問題文**:
> 西暦2050年、AIが当たり前になった世界での自社忘年会の乾杯挨拶を、少し笑える雰囲気で書いてください。

**採点**:
- Claudeが0〜25点（整数）
- 5観点: originality / clarity / relevance / insight / humor
**回答長**:
- 140文字以内

---

## 7) 結果表示

### 運営チャンネル（#test）
- Top10表示
- `名前 + 点数 + コメント + O/C/R/I/H`

### フォーラム投稿内
- **Top3のみ**
- 「Claudeが採点」明示
- **控えめでやさしいトーン**（例: 「おつかれさまです。トップ3はこちらです！」）
- 回答抜粋 + コメント + O/C/R/I/H

※ フォーラム投稿（出題/結果）は **Webhook送信**に切り替え済み。  
  そのため **アイコンと表示名はWebhookの設定に従う**。

---

## 8) Claude採点の実装

`src/agents/quiz_master/scoring.py`
- Anthropic Messages APIを `httpx` で叩く
- **JSONのみ**で返させる
- 想定出力:
  - `total`（0〜points）
  - `criteria`（5観点）
  - `comment`（短評）

パース失敗時:
- 0点扱い
- `meta.parse_error` に記録

---

## 9) DailyReporterの扱い

- **誤投稿防止のため通常運用ではOFF**
- `.env`:
  - `ENABLE_DAILY_REPORTER=0`
- 1回だけ走らせたい場合:
```
python main.py --once
```

---

## 10) トラブルシュート（頻出）

### ① 問題が二重投稿される
- 原因: **Botが二重起動**（Docker + ローカル）
- 対策:
```
docker compose down
# ローカル python プロセスも止める
```

### ② `!quiz` が反応しない
- 原因: `.env` 改行欠落 or `QUIZ_ADMIN_USER_IDS` 未設定
- 対策: `.env` を確認して再起動

### ③ フォーラム投稿が作られない
- 原因: `QUIZ_FORUM_CHANNEL_ID` 未設定 / 権限不足

### ④ InviteRoleAssignerが起動で落ちる
- 原因: `INVROLE_*` の不足

---

## 11) 最小動作テスト
```
!quiz reset
!quiz start
# フォーラム投稿内に回答
!quiz close
```
期待:
- #test とフォーラム投稿内に結果が出る

---

## 12) 明日（本番）の前提
- **2025-12-20 18:00–21:00（3時間）**
- 40名同時参加を想定
- `QUIZ_DEBUG` は **OFF**
- `ENABLE_DAILY_REPORTER=0`
- 40人集中時はDiscord遅延が起きるので、`!quiz close` は数十秒待つのが安全

---

## 13) 関連ファイル（非Quiz）
```
src/agents/invite_role_assigner/config.py
src/agents/invite_role_assigner/logic.py
src/agents/daily_reporter/logic.py
src/agents/operator/logic.py
src/core/action_registry.py
src/core/bot.py
```
