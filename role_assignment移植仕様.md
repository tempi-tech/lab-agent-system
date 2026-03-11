# role_assignment移植仕様

作成日: 2026-03-11
対象移植先: `/Users/kai/.openclaw` 系 runtime
対象能力:

- `invite_role_assigner`
- `membership_checker` のうち role lifecycle に必要な部分

移植方針: **Discord の権限条件と監査ログを先に再現し、付与実行は最後に切り替える**

## 1. この機能の役割

新規参加者がどの invite で入ったかを推定し、
Discord ロールを付与する。

あわせて:

- 参加ログを保存する
- phase 別の統計を出す
- note 会員 CSV と照合して role sync を行う
- 権限 / role hierarchy 問題を診断する

## 2. 現行コード上の入口

- join event:
  - [src/agents/invite_role_assigner/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/invite_role_assigner/logic.py)
- membership commands / scheduled check:
  - [src/agents/membership_checker/logic.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/membership_checker/logic.py)
  - [src/agents/membership_checker/checker.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/membership_checker/checker.py)
- config:
  - [src/agents/invite_role_assigner/config.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/invite_role_assigner/config.py)
  - [src/agents/membership_checker/config.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/membership_checker/config.py)
- diagnostics:
  - [src/agents/invite_role_assigner/role_checks.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/invite_role_assigner/role_checks.py)
- persistence:
  - [src/agents/invite_role_assigner/storage.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/invite_role_assigner/storage.py)
  - [src/agents/invite_role_assigner/sync_allowlist.py](/Users/kai/Develop/autogen/lab-agent-system/src/agents/invite_role_assigner/sync_allowlist.py)

## 3. 役割分解

### A. Join-time role assignment

- `on_member_join`
- invite uses 差分から used code を推定
- `general_role` は基本付与
- invite 検出失敗時は `review_role` を追加付与
- 付与前に権限/階層チェック
- 監査ログ + CSV 永続化

### B. Invite/admin operations

- phase 一覧確認
- invite 生成
- invite 全削除
- status チェック
- sync preview / execute

### C. Membership lifecycle

- CSV から active member を読む
- role 未付与者を preview / assign
- note 退会者との sync preview / execute

## 4. 入力

### 4-1. 設定

最重要:

- `INVROLE_CONFIG`
  - JSON config 優先

JSON には少なくとも:

- `guild_id`
- `log_channel_id`
- `roles.general_role_id`
- `roles.review_role_id`
- `roles.admin_role_id`
- `phases`
  - phase name
  - description
  - `invite_codes`

fallback として legacy env あり:

- `INVROLE_GUILD_ID`
- `INVROLE_GENERAL_ROLE_ID`
- `INVROLE_REVIEW_ROLE_ID`
- `INVROLE_ADMIN_ROLE_ID`
- `INVROLE_LOG_CHANNEL_ID`
- `INVROLE_INVITED_CODES`

### 4-2. Membership 側入力

- `MEMBERSHIP_CHECKER_LOG_CHANNEL_ID`
- `MEMBERSHIP_CHECKER_CSV_DIR`
- `MEMBERSHIP_CHECKER_DATA_DIR`
- `MEMBERSHIP_CHECKER_CONFIRM_USERNAMES`

Membership config は `invite_role_assigner` の JSON を参照して guild/role id を継承する。

### 4-3. 外部入力

- Discord join event
- Discord guild invites
- note membership CSV

## 5. Join-time role assignment の現仕様

### Step 1. 起動時 baseline

`on_ready` で:

- guild 取得
- `guild.invites()` を呼び baseline uses を保持
- role diagnostics を先に実施

### Step 2. member join

`on_member_join` で:

- guild id を確認
- lock を取る
- `_detect_used_invite_code()` を実行

invite 検知ロジック:

- baseline と current invites を比較
- `uses` が 1 増えた code が 1本なら採用
- change がなく vanished code が 1本なら採用
- retry: `0s`, `0.8s`, `1.6s`

### Step 3. role 決定

- `general_role` は常に候補
- code が phase に属すなら `招待枠 (phase)`
- code はあるが phase 外なら `一般枠`
- code 不明なら `検出失敗`
  - このとき `review_role` を追加候補

### Step 4. 付与前 diagnostics

チェック項目:

- bot に `Manage Roles` があるか
- bot top role が対象 role より上か
- role が managed role ではないか

issue があれば:

- role assignment を skip
- Discord log channel に診断ログを出す

### Step 5. 実付与

- `member.add_roles(*roles_to_add, reason=...)`
- `discord.Forbidden` は明示ログ
- その他例外もログ

### Step 6. 永続化

`member_log.csv` に記録:

- `timestamp`
- `discord_id`
- `username`
- `invite_code`
- `phase`
- `role_assigned`
- `detection_method`

`detection_method`:

- `uses_diff`
- `vanished`
- `detection_failed`

## 6. Membership / sync の現仕様

### Status

- 最新 `note_active_*.csv` を探す
- Discord ID が有効な会員 / username only を分類
- サーバー参加状況と role 保持状況を確認

### Assign

- active member で `general_role` 未保持者を抽出
- preview / execute
- `confirm-usernames` で username matching を追加

### Sync

- note 側 active member の Discord ID 集合を作る
- `general_role` 保持者との差分を取る
- `sync_allowlist.json` を除外
- preview / execute
- execute 時は remove_roles

### Scheduled check

- GitHub Actions で週次実行
- 現状は status report のみ

## 7. 依存と前提

### Discord 権限

Join / sync の再現には最低限:

- `Manage Roles`
- `Manage Guild` または invites 取得権限相当
- `Create Invite`（管理コマンドを使うなら）
- log channel 送信権限

### Discord role hierarchy

- bot top role が `general_role` と `review_role` より上であること

### データ

- `data/invite_role_assigner/config.json`
- `data/invite_role_assigner/member_log.csv`
- `data/invite_role_assigner/sync_allowlist.json`
- note membership CSV directory

## 8. OpenClaw 側で保持すべき仕様

### 必須

- invite baseline refresh
- join event から invite code 推定
- `general_role` / `review_role` 判定
- diagnostics-first の role assignment
- join log の永続化
- sync allowlist
- preview と execute の分離

### あるとよい

- phase 別統計
- invite generation / delete 管理コマンド
- membership weekly status report

### 後回し可

- username-based role assign
- followup export

## 9. OpenClaw 実装方針

おすすめの分解:

1. `refresh_invite_baseline`
2. `detect_used_invite_code`
3. `build_role_assignment_plan`
4. `run_role_diagnostics`
5. `apply_role_assignment`
6. `append_join_audit_log`
7. `preview_membership_sync`
8. `execute_membership_sync`

## 10. Guardrails

### 初期段階

- まず **diagnostics + shadow log only**
- 実際の add/remove roles はまだしない

### 実付与開始前

- Discord 側 role hierarchy を再確認
- log channel で failure が観測できること
- rollback 手順を決める

### rollback

- OpenClaw 側 role mutation を止める kill switch
- 旧系を一時的に主系へ戻す手順

## 11. 切替順

1. OpenClaw で diagnostics-only 実装
2. Join event の invite detection 精度確認
3. Shadow log で role plan を比較
4. 限定対象で add_roles を有効化
5. 問題なければ全面切替
6. sync/remove_roles は最後

`remove_roles` の方が事故コストが高いので、最も遅く切り替える。

## 12. 失敗しやすい点

- invite baseline が stale
- Discord invites 取得が Forbidden
- role hierarchy が不正
- managed role を対象にしている
- membership CSV のフォーマット揺れ
- allowlist を見落とす
- OpenClaw 側で join event の即時性が落ちる
- diagnostics を作る前に add/remove roles を有効化する

