# OSS公開向けサニタイズ計画（2025-12-22）

## ゴール
コミュニティ固有データ（ID・招待コード・ログ・実投稿URL等）を完全に除去し、  
OSSとして公開できる状態に整える。

## 範囲
- **対象**: docs/・scripts/・data/・サンプルJSON・README/AGENTS  
- **除外**: 実運用の `.env`（すでにgitignore対象）

---

## サニタイズ方針（統一ルール）
1. **実ID（17〜19桁の数字）・招待コード**はすべて削除または置換。
2. **実サーバURL**（discord.com/channels/…）はサンプルURLに置換。
3. **運用ログ（回答内容・ユーザーID）**は削除 or 公開版に要約。
4. 画像/素材は **権利確認**。不明なら削除 or 置換。

置換ルール（例）:
- Guild ID → `GUILD_ID`
- Channel ID → `CHANNEL_ID`
- Role ID → `ROLE_ID`
- User ID → `USER_ID`
- Invite code → `INVITE_CODE`
- `https://discord.com/channels/...` → `https://discord.com/channels/GUILD_ID/CHANNEL_ID`

---

## 重要ファイルの対応

### A. **必ず削除 or 非公開**
- `data/quiz_master/session.json`  
  → 実回答/ユーザーID/採点ログが含まれるため **削除**  
  → `.gitignore` に `data/` を追加

### B. **強くマスク推奨（実IDが含まれる）**
- `docs/invite_role_assigner_implementation_log.md`  
  → 実Guild/Role ID / 招待コードあり  
  → **全てダミー置換**

- `docs/system_snapshot_2025-12-19.md`  
  → 実チャンネルID等あり → 置換

- `docs/quiz_forum_overview.md`  
  → 実IDあり → 置換

- `docs/quiz_forum_inspect.md`  
  → 実IDあり → 置換

- `docs/smoke_test.md`  
  → 実IDあり → 置換

- `docs/forum_quiz_troubleshooting.md`  
  → 実IDとユーザーIDあり → 置換

- `docs/implementation_log_2025-12-19.md`  
  → 実チャンネルIDあり → 置換

- `docs/role-agent-implementation.md`  
  → 一部実IDあり → 置換

- `scripts/announce_update.py`  
  → 実サーバURLあり → 置換（サンプルURLに）

### C. **要確認（著作権/権利）**
- `src/agents/quiz_master/Airi.jpg`  
  → 画像利用許諾の確認が必要。  
  → 許諾なしなら `placeholder.jpg` に置換。

- `scripts/geminiupdate.png`  
  → 権利不明。不要なら削除。

---

## 実装ステップ（順序）

### 1) `.gitignore` に `data/` を追加
```diff
.env
*.env
data/
```

### 2) `data/quiz_master/session.json` を削除
```bash
rm -f data/quiz_master/session.json
```
※ もしGit管理されていれば `git rm --cached` で除外

### 3) docs/smoke_test.md 等のIDを一括置換
- 対象リスト（上記B）を **全てダミー化**

### 4) `scripts/announce_update.py` のURLをサンプル化
```diff
- https://discord.com/channels/GUILD_ID/CHANNEL_ID
```

### 5) 画像・素材の扱いを決定
- `Airi.jpg` / `geminiupdate.png` を残すなら **ライセンス明記**
- 不明なら削除し README に「任意画像」の記載

### 6) 最終検証（漏れチェック）
```bash
rg -n "[0-9]{17,}" .
rg -n "discord.com/channels" .
rg -n "INVROLE_.*=" docs scripts
```
→ ヒットが **ダミーIDのみ**であることを確認。

---

## 受け入れ条件（成功基準）
- `.env`・APIキー・実ID・招待コードがリポジトリに残っていない
- `data/` がgit管理対象外になっている
- docs/scripts内のDiscordリンクがサンプル化されている
- 画像素材の権利が明示されるか、削除されている

---

## 実装開始前の確認事項
- Airi.jpg の権利をどうするか？（残す/削除）
- 既存の実運用ログを **削除** or **マスク**のどちらで進めるか
