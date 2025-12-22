# フォーラム出題テストの不具合メモ（原因と対策）

## 事象
- `!quiz start` を打っても **フォーラム投稿が作られず**、
  旧仕様の **スレッド形式**で #test に問題が出続けた。
- 一時期、**運営コマンドに反応しない**状態も発生。

## 原因
### 1) 旧Bot（Docker）が起動したまま
- Docker で起動していた旧Botが **古いコード**で反応。
- 新しいフォーラム版の挙動が見えなかった。

### 2) `.env` の改行欠落
- `.env` に以下のような行が存在：
  ```
  QUIZ_ADMIN_ROLE_IDS=ROLE_IDQUIZ_ADMIN_USER_IDS=USER_ID
  ```
- これにより `QUIZ_ADMIN_USER_IDS` が正しく読み込まれず、
  **admin判定に失敗 → コマンドが無視**されていた。

## 対策
- **Docker Bot を停止**して新Botのみを起動
  ```bash
  docker compose down
  ```
- `.env` の改行を修正
  ```
  QUIZ_ADMIN_ROLE_IDS=ROLE_ID
  QUIZ_ADMIN_USER_IDS=USER_ID
  ```

## 確認結果
- フォーラム `コンテスト` に問題投稿が作成され、
  投稿内で回答が受け付けられることを確認。
