# 実装計画：Q2 20点化・早押しタイム表示・全員表示・抽選削除（2025-12-19）

## 目的（要求整理）
- **Q2** は「面白さ」なしの **4観点**にして **20点満点**にする。
- **Q1/Q2 は早押し**：結果表示に **回答タイム**を出す。
- **Q1** は観点なし・**タイムでランキング**（Q2は点数＋タイム併記）。
- **Q1〜Q3すべて**：結果は **トップ3ではなく全員表示**。
- **抽選賞（ラッキー賞）を削除**。

---

## 現状の該当箇所
- Q2の問題定義: `src/agents/quiz_master/quizzes/bonenkai_2025.json`
- LLM採点テンプレ: `src/agents/quiz_master/scoring.py#score_knowledge_answers`
- 結果出力: `src/agents/quiz_master/agent.py#_post_question_result`
- 抽選賞: `src/agents/quiz_master/agent.py#_close_and_grade`
- セッション保存: `src/agents/quiz_master/storage.py`

---

## 変更方針（概要）
1) **Q2の配点・観点を4つに**  
2) **早押しタイムを保存・表示**  
3) **全員の結果を出す（フォーラム/運営）**  
4) **抽選賞を削除**  

---

## 詳細変更（ファイル別）

### 1) Q2（問題定義）を更新
**対象**: `src/agents/quiz_master/quizzes/bonenkai_2025.json`

- Q2の `points` を **20** に変更
- Q2の `rubric` から「面白さ」を除外
- Q2のみ **観点リスト**を明示するため `criteria` を追加  

**差分案（抜粋）**
```diff
{
  "id": "k2",
  "type": "knowledge",
  "grading": "llm",
  "title": "サム・アルトマンの「悪夢」",
  "prompt": "...",
- "points": 25,
+ "points": 20,
  "time_limit_sec": 180,
- "rubric": "- 0〜25点（整数）。\n- ...\n- 5観点（独創性/明快さ/お題適合/洞察/面白さ）..."
+ "rubric": "- 0〜20点（整数）。\n- ...\n- 4観点（独創性/明快さ/お題適合/洞察）を各0〜5点で付与する。",
+ "criteria": ["originality", "clarity", "relevance", "insight"]
}
```

---

### 2) Question に criteria を追加
**対象**: `src/agents/quiz_master/config.py`

- `Question` に `criteria: List[str] = field(default_factory=list)` を追加  
- JSON `criteria` を読み込む  
- 未指定の場合は **従来の5観点**にフォールバック

**差分案（抜粋）**
```diff
@dataclass
class Question:
    ...
    rubric: str = ""
    max_chars: int = 600
+   criteria: List[str] = field(default_factory=list)

@staticmethod
def from_dict(d):
    ...
    return Question(
        ...
        rubric=str(d.get("rubric", "")),
        max_chars=int(d.get("max_chars", 600)),
+       criteria=list(d.get("criteria", [])),
    )
```

---

### 3) LLM採点プロンプトを「観点数可変」に
**対象**: `src/agents/quiz_master/scoring.py`

- `score_knowledge_answers()` / `score_creative_answers()` に  
  `criteria_keys: List[str]` を追加し、**観点数に合わせて指示文を変更**
- Q2は `criteria_keys=["originality","clarity","relevance","insight"]`
- Q3は従来通り5観点

**差分案（抜粋）**
```diff
async def score_knowledge_answers(..., criteria_keys: List[str] | None = None):
    criteria_keys = criteria_keys or ["originality","clarity","relevance","insight","humor"]
    ...
- 5観点（独創性/明快さ/お題適合/洞察/面白さ）で各0〜5点
+ {len(criteria_keys)}観点（...）で各0〜5点
```

---

### 4) 早押しタイムの保存・表示
**対象**: `src/agents/quiz_master/storage.py`, `src/agents/quiz_master/agent.py`

#### 保存
- `QuizSessionState` に `question_opened_at: float` を追加  
- `_post_next_question()` で `st.question_opened_at = time.time()` をセット

#### 表示
- 各投稿の `Submission.created_at` との差分で **回答タイム**を算出  
- `Q1/Q2` は **結果出力に time を必ず表示**
- **Q1はタイム優先**でランキングソート  
  （`Q2` は 点数→タイムの順でソート）

**差分案（抜粋）**
```diff
class QuizSessionState:
    ...
    question_opened_at: float = 0.0

_post_next_question():
    st.question_opened_at = time.time()

_post_question_result():
    elapsed = sub.created_at - st.question_opened_at
    time_text = f\"t={elapsed:.1f}s\"
```

---

### 5) 結果表示は「全員」に
**対象**: `src/agents/quiz_master/agent.py#_post_question_result`

- `top3` を廃止 → **全員**を出す  
- 文字数が2000を超える場合は **分割送信**  

**差分案（抜粋）**
```diff
- top3 = ranking[:3]
- for uid, pts in top3:
+ for uid, pts in ranking:
```

---

### 6) 抽選賞の削除
**対象**: `src/agents/quiz_master/agent.py#_close_and_grade`

- ラッキー賞の `deterministic_draw` ブロックを削除

**差分案（抜粋）**
```diff
- candidates = ...
- lucky = deterministic_draw(...)
- if lucky: ...
```

---

## 実装後の想定出力
- **Q1**: `1位: user — 25pt t=12.3s`
- **Q2**: `user — 17pt O4 C4 R5 I4 t=28.7s`
- **Q3**: `user — 21pt O4 C5 R4 I4 H4`

---

## テスト手順（最小）
1. `!quiz reset`
2. `!quiz load bonenkai_2025.json`
3. `!quiz start` → すぐ回答
4. `!quiz close`
5. **Q1の結果に time が出ること**を確認
6. `!quiz next` → Q2 → すぐ回答 → `!quiz close`
7. **Q2が20点満点・4観点**になっていることを確認
8. `!quiz next` → Q3 → 回答 → `!quiz close`
9. **全員が出力されること**を確認

---

## 影響範囲
- `bonenkai_2025.json` のQ2構成
- LLM採点テンプレの観点数
- 結果表示ロジック
- 保存データ構造（`question_opened_at` 追加）
