# Quiz フォーラム運用 Inspect（動作確認手順）

## 目的
- フォーラム出題・回答収集・Claude採点までを一通り検証する。

## 前提
- `QUIZ_ADMIN_CHANNEL_ID=CHANNEL_ID`
- `QUIZ_FORUM_CHANNEL_ID=FORUM_CHANNEL_ID`
- `QUIZ_LLM_PROVIDER=claude`
- `ANTHROPIC_API_KEY` が設定済み
- Botは**1つだけ**起動（多重起動は二重投稿の原因）

## 手順
### 1) 起動確認
- Bot がオンラインで、#test で `!quiz reset` に反応すること

### 2) 出題（フォーラム）
```
!quiz reset
!quiz start
```
- #test に「フォーラムに出題しました: …」が出る
- フォーラム「コンテスト」に **Q1** 投稿が作成される

### 3) クリエイティブ問題まで進める
```
!quiz close
!quiz next
!quiz close
!quiz next
```
- Q3（クリエイティブ）がフォーラムに投稿される

### 4) 回答 & 採点
- フォーラム投稿内に回答を書き込む
- #test で `!quiz close`
- 結果に **O/C/R/I/H の観点スコア + コメント**が出る
- **フォーラム投稿内にも結果が出る**

## 期待結果（例）
- #test: `📊 結果: ... user: 18pt — コメント O4 C3 R4 I3 H4`
- フォーラム投稿内: 同様の結果が表示される

## よくある失敗
- 二重投稿 → Botが二重起動している
- コマンド無反応 → `QUIZ_ADMIN_USER_IDS` の未設定 or `.env` 改行崩れ
- フォーラム投稿が作られない → `QUIZ_FORUM_CHANNEL_ID` 未設定/権限不足
