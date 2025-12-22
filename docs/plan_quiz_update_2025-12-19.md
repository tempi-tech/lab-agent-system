# 実装計画：Quiz 3問構成 + Claude採点拡張（2025-12-19）

## ゴール
- 明日は **Dockerで `quiz_master` + `invite_role_assigner` 同時稼働**。
- DailyReporter は **起動しない**。
- クイズは **3問構成（自由入力）**：
  1) EQベンチ1位（正誤固定・誤答はClaudeコメント）
  2) サム・アルトマン自伝（Claude採点・部分点）
  3) クリエイティブ（Claude採点・新rubric）
- 結果は **#test とフォーラム投稿の両方**に出力。
- フォーラム側は **知的な女の子トーン**＋「Claudeが5観点で採点」明示＋トップ3。

---

## 変更対象ファイル
- `main.py`（DailyReporterの起動制御）
- `src/agents/quiz_master/agent.py`（出題・採点・結果表示）
- `src/agents/quiz_master/scoring.py`（Claude採点の拡張）
- `src/agents/quiz_master/config.py`（Questionにgrading追加）
- `src/agents/quiz_master/quizzes/bonenkai_2025.json`（3問構成へ更新）
- `src/agents/quiz_master/README.md`（運用・env追記）
- `.env.example`（運用env追記）

---

## 実装ステップ（具体）

### 1) DailyReporterの起動制御
- `ENABLE_DAILY_REPORTER=0` で通常起動時に登録しない
- `--once` 実行時は **常にDailyReporterのみ**起動

### 2) Quiz JSON を3問構成に変更
- **Q1 (knowledge / exact)**
  - prompt: EQベンチ1位モデル
  - accepted_answers: `Kimi-K2-Instruct`
  - 判定: 大文字/小文字は無視、空白/ハイフンは許容
  - grading: `exact`
  - 不正解時は固定コメント（LLMは使わない）
- **Q2 (knowledge / llm)**
  - prompt: 「18歳のサム・アルトマンが…」
  - rubric: 正解要点「短期間で集中的にプログラミングを学ぶ挑戦」
  - grading: `llm`
- **Q3 (creative / llm)**
  - prompt: 固定お題（新rubric）
  - rubric: 5観点（均等）
- `allow_answer_edit=false`

### 3) 知識問題の採点拡張
- `Question.grading` を追加（`exact` / `llm`）
- `exact`:
  - 正誤判定のみ（満点 or 0）
  - 不正解はClaudeで短評生成
- `llm`:
  - Claudeで部分点（0〜満点）
  - 5観点スコア + コメントのJSON

### 4) 結果表示の強化
- #test: 簡易スコア一覧
- フォーラム投稿内:
  - 冒頭に「Claudeが5観点で採点」明示
  - トーンは「知的な女の子（ふん、やるじゃない）」
  - **トップ3のみ**表示
  - 回答の短い抜粋 + 観点スコア + コメント

---

## 未確定の挿入箇所
- 今回は未確定事項なし（Q2の要点も確定済み）。

---

## 受け入れ条件
- Q1〜Q3 がフォーラムに投稿される
- Q2/Q3 は Claude採点で 5観点スコア＋コメントが出る
- Q1 は正誤判定＋不正解理由コメント
- #test とフォーラム両方に結果が出る
- フォーラム表示はトップ3のみ・知的な女の子トーン
