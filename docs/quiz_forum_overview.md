# Quiz フォーラム運用の全体像

このドキュメントは、**QuizMaster がフォーラムでクイズを運用する仕組み**を、
このディレクトリに初めて触れる人でも理解できるようにまとめたものです。

---

## 1. 全体構成（どのファイルが何をするか）

```
src/agents/quiz_master/
├── agent.py          # Discordイベント処理・出題・採点の本体
├── scoring.py        # LLM採点（Gemini / Claude）とJSONパース
├── storage.py        # セッション状態の保存（data/quiz_master/session.json）
├── config.py         # クイズJSON読み込み用データ構造
├── utils.py          # コマンド解析や共通ユーティリティ
├── README.md         # 使い方の概要
└── quizzes/
    └── bonenkai_2025.json  # 問題データ（例）

.data/
└── quiz_master/
    └── session.json  # 実行中セッションの保存先（自動生成）
```

---

## 2. クイズの出題はどこで行われる？

- **`agent.py` の `_post_next_question()`** が出題処理の中心です。
- `QUIZ_FORUM_CHANNEL_ID` のフォーラムに **新しい投稿（スレッド）** を作成します。
- 投稿タイトルは `Q{番号}: {タイトル}` の形式です。
- 投稿本文は **問題文＋選択肢＋回答案内**がそのまま入ります。

> つまり、**「フォーラム投稿＝問題1つ」** という構成です。

---

## 3. 回答はどこに送る？

- 参加者は **フォーラム投稿内に返信**します。
- Botは `thread_id` を記録しており、
  **その投稿内のメッセージだけを回答として採用**します。

---

## 4. 運営はどこで操作する？

- `QUIZ_ADMIN_CHANNEL_ID` に指定した**運営チャンネル**のみで
  `!quiz` コマンドが有効です。
- 例: `!quiz start`, `!quiz next`, `!quiz close`, `!quiz leaderboard` など

---

## 5. どこで問題が管理されている？

- 問題は **JSONファイル**で管理します。
- デフォルトは：
  - `src/agents/quiz_master/quizzes/bonenkai_2025.json`
- `!quiz load <path>` で別のJSONに切り替え可能。

### JSONの例（creative問題）
```json
{
  "id": "c1",
  "type": "creative",
  "title": "プロンプト・バトル①",
  "prompt": "...",
  "points": 25,
  "max_chars": 700,
  "rubric": "- 0〜25点..."
}
```

---

## 6. 採点はどう行われる？

### 知識問題（knowledge）
- `agent.py` の `_grade_knowledge()` が採点。
- `correct_option` または `accepted_answers` と一致すれば満点。

### クリエイティブ問題（creative）
- `scoring.py` で **LLMに採点依頼**。
- `QUIZ_LLM_PROVIDER=claude` の場合は Claudeを利用。

**採点結果は以下で返る：**
- **total点（0〜points）**
- **5観点スコア**
  - originality / clarity / relevance / insight / humor
- **短いコメント**

結果は #test などの運営チャンネルに出力され、
**同時にフォーラム投稿内にも結果が表示**されます。

---

## 7. セッション情報はどこに保存される？

- `data/quiz_master/session.json`
- `storage.py` の `JsonStore` が保存・読み込みを担当。
- `!quiz reset` で状態をリセット。

---

## 8. 使う環境変数一覧（最低限）

```
QUIZ_ADMIN_USER_IDS=（運営のユーザーID）
QUIZ_ADMIN_CHANNEL_ID=CHANNEL_ID           # 運営チャンネル
QUIZ_FORUM_CHANNEL_ID=FORUM_CHANNEL_ID     # フォーラム
QUIZ_LLM_PROVIDER=claude
QUIZ_CLAUDE_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=xxxxxx
```

---

## 9. 実行の流れ（運営視点）

1. `!quiz reset`
2. `!quiz start`
3. `!quiz next`（必要に応じて）
4. フォーラム投稿内で回答
5. `!quiz close` → 採点結果
6. `!quiz leaderboard` / `!quiz end`

---

## 10. よくある原因と対策

- **問題が2重に出る**
  - Botが二重起動している可能性が高い

- **コマンドに反応しない**
  - `QUIZ_ADMIN_USER_IDS` が未設定 or `.env`の改行崩れ

- **フォーラム投稿が作れない**
  - `QUIZ_FORUM_CHANNEL_ID` が未設定
  - Botにフォーラム投稿権限がない

---

## 関連ドキュメント
- `docs/quiz_forum_implementation_log_2025-12-19.md`
- `docs/quiz_forum_inspect.md`
- `docs/forum_quiz_troubleshooting.md`
